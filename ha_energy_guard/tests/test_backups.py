"""Backup, verification and rollback tests.

The promise these tests pin down:

* a backup of every statistic that is about to change exists **before** the
  recorder is asked to change anything,
* the backup contains the original rows, not the repaired ones,
* the repair response always contains a usable rollback offset,
* verification only reports success after the recorder read the change back.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.energy_guard.backups import (
    MAX_BACKUP_ROWS,
    async_backup_statistics,
)
from custom_components.energy_guard.models import RepairRequest
from custom_components.energy_guard.statistics import async_repair_statistics

from .conftest import setup_guard

STAT = "energy_guard_test:backup"
FALSE_ENERGY = 38243.46
START = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=6)


@pytest.fixture(autouse=True)
def recorder(recorder_mock):
    """Run every test in this module against an in-memory recorder."""
    return recorder_mock


async def _seed(hass: HomeAssistant) -> None:
    """Seed a statistic with the reconnect corruption at index 3."""
    rows = []
    for index in range(6):
        if index < 3:
            state = total = FALSE_ENERGY
        elif index == 3:
            state, total = 0.0, FALSE_ENERGY
        else:
            state, total = FALSE_ENERGY, FALSE_ENERGY * 2
        rows.append(
            {"start": START + timedelta(hours=index), "state": state, "sum": total}
        )

    async_add_external_statistics(
        hass,
        {
            "has_mean": False,
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": STAT,
            "source": STAT.split(":")[0],
            "statistic_id": STAT,
            "unit_class": "energy",
            "unit_of_measurement": "kWh",
        },
        rows,
    )
    await get_instance(hass).async_block_till_done()
    await hass.async_block_till_done()


async def _rows(hass: HomeAssistant) -> list[dict]:
    """Read the statistic back."""
    return (
        await hass.async_add_executor_job(
            statistics_during_period,
            hass,
            START - timedelta(hours=1),
            None,
            {STAT},
            "hour",
            None,
            {"sum", "state"},
        )
    )[STAT]


def _backup_files(hass: HomeAssistant) -> list[Path]:
    """Return the backup files of this config dir (executor call in tests)."""
    return sorted(
        Path(hass.config.config_dir, "energy_guard", "backups").glob("*.json")
    )


async def test_backup_contains_the_original_rows(hass: HomeAssistant) -> None:
    """A backup holds the pre-change values and a checksum."""
    await setup_guard(hass)
    await _seed(hass)
    before = await _rows(hass)

    backup = await async_backup_statistics(
        hass,
        directory=Path(hass.config.config_dir, "energy_guard", "backups"),
        statistic_id=STAT,
        unit="kWh",
        start=START,
        end=dt_util.utcnow(),
        reason="unit test",
    )
    path = Path(backup["file"])
    payload = json.loads(await hass.async_add_executor_job(path.read_text))
    await hass.async_block_till_done()

    assert payload["energy_guard"]["reason"] == "unit test"
    assert payload["energy_guard"]["checksum"] == backup["checksum"]
    assert payload["energy_guard"]["checksum"].startswith("sha256:")
    assert payload["energy_guard"]["restore_hint"]
    assert [row["sum"] for row in payload["rows"]] == [row["sum"] for row in before]
    assert len(payload["rows"]) == len(before)
    assert len(payload["rows"]) <= MAX_BACKUP_ROWS


async def test_backup_is_written_before_the_recorder_changes_anything(
    hass: HomeAssistant,
) -> None:
    """The file with the old values exists even if the repair is a preview only."""
    await setup_guard(hass)
    await _seed(hass)
    start_time = START + timedelta(hours=4)

    report = await async_repair_statistics(
        hass,
        requests=[
            RepairRequest(
                statistic_id=STAT,
                start_time=start_time,
                offset=-FALSE_ENERGY,
                unit="kWh",
            )
        ],
        confirm=False,  # preview
        dry_run=False,
        create_backup=True,
        verify=False,
        backup_dir=Path(hass.config.config_dir, "energy_guard", "backups"),
    )
    await hass.async_block_till_done()

    assert report.status == "preview"
    assert report.backups == []  # previews never write a backup
    assert report.rollback[0]["offset"] == pytest.approx(FALSE_ENERGY)
    assert await hass.async_add_executor_job(_backup_files, hass) == []

    # Confirmed repair: backup first, then the change.
    report = await async_repair_statistics(
        hass,
        requests=[
            RepairRequest(
                statistic_id=STAT,
                start_time=start_time,
                offset=-FALSE_ENERGY,
                unit="kWh",
            )
        ],
        confirm=True,
        dry_run=False,
        create_backup=True,
        verify=True,
        backup_dir=Path(hass.config.config_dir, "energy_guard", "backups"),
    )
    await hass.async_block_till_done()

    files = await hass.async_add_executor_job(_backup_files, hass)
    assert len(files) == 1
    payload = json.loads(await hass.async_add_executor_job(files[0].read_text))
    assert [row["sum"] for row in payload["rows"]][-1] == pytest.approx(
        FALSE_ENERGY * 2
    )
    assert report.applied[0].backup_file == str(files[0])
    assert report.applied[0].verified is True
    assert report.applied[0].after_value == pytest.approx(FALSE_ENERGY)
    assert report.rollback[0]["backup_file"] == str(files[0])
    assert "undo" in report.rollback[0]["message"]


async def test_repair_can_be_rolled_back_with_the_returned_offset(
    hass: HomeAssistant,
) -> None:
    """Applying the inverse offset restores the original values."""
    await setup_guard(hass)
    await _seed(hass)
    start_time = START + timedelta(hours=4)
    before = (await _rows(hass))[-1]["sum"]
    backup_dir = Path(hass.config.config_dir, "energy_guard", "backups")

    repair = await async_repair_statistics(
        hass,
        requests=[
            RepairRequest(
                statistic_id=STAT,
                start_time=start_time,
                offset=-FALSE_ENERGY,
                unit="kWh",
            )
        ],
        confirm=True,
        dry_run=False,
        create_backup=True,
        verify=True,
        backup_dir=backup_dir,
    )
    after = (await _rows(hass))[-1]["sum"]
    assert after == pytest.approx(before - FALSE_ENERGY)

    rollback = repair.rollback[0]
    undone = await async_repair_statistics(
        hass,
        requests=[
            RepairRequest(
                statistic_id=STAT,
                start_time=start_time,
                offset=rollback["offset"],
                unit=rollback["unit"],
                reason="rollback test",
            )
        ],
        confirm=True,
        dry_run=False,
        create_backup=True,
        verify=True,
        backup_dir=backup_dir,
    )
    assert undone.applied[0].verified is True
    assert (await _rows(hass))[-1]["sum"] == pytest.approx(before)
    assert len(await hass.async_add_executor_job(_backup_files, hass)) == 2


async def test_dry_run_changes_nothing(hass: HomeAssistant) -> None:
    """dry_run produces the full report but never touches the recorder."""
    await setup_guard(hass)
    await _seed(hass)
    before = await _rows(hass)

    report = await async_repair_statistics(
        hass,
        requests=[
            RepairRequest(
                statistic_id=STAT,
                start_time=START + timedelta(hours=4),
                offset=-FALSE_ENERGY,
                unit="kWh",
            )
        ],
        confirm=True,
        dry_run=True,
        create_backup=True,
        verify=True,
        backup_dir=Path(hass.config.config_dir, "energy_guard", "backups"),
    )
    await hass.async_block_till_done()

    assert report.status == "preview"
    assert report.applied == []
    assert report.preview[0].expected_value is not None
    assert await _rows(hass) == before
    assert await hass.async_add_executor_job(_backup_files, hass) == []


async def test_backups_are_pruned_to_the_configured_count(hass: HomeAssistant) -> None:
    """Only the newest backups are kept, so the config dir cannot grow forever."""
    await setup_guard(hass)
    await _seed(hass)
    directory = Path(hass.config.config_dir, "energy_guard", "backups")
    await hass.async_add_executor_job(
        lambda: directory.mkdir(parents=True, exist_ok=True)
    )

    for index in range(4):
        await async_backup_statistics(
            hass,
            directory=directory,
            statistic_id=f"energy_guard_test:stat_{index}",
            unit="kWh",
            start=START,
            end=dt_util.utcnow(),
            keep=2,
        )
    await hass.async_block_till_done()

    files = await hass.async_add_executor_job(_backup_files, hass)
    assert len(files) == 2


async def test_repair_is_skipped_when_the_backup_cannot_be_written(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """A statistic that cannot be backed up is never changed."""
    await setup_guard(hass)
    await _seed(hass)
    before = await _rows(hass)

    # A backup directory that cannot exist: its parent is a regular file.
    blocked_parent = tmp_path / "not_a_directory"
    await hass.async_add_executor_job(blocked_parent.write_text, "file")
    broken_dir = blocked_parent / "backups"

    report = await async_repair_statistics(
        hass,
        requests=[
            RepairRequest(
                statistic_id=STAT,
                start_time=START + timedelta(hours=4),
                offset=-FALSE_ENERGY,
                unit="kWh",
            )
        ],
        confirm=True,
        dry_run=False,
        create_backup=True,
        verify=True,
        backup_dir=broken_dir,
    )
    await hass.async_block_till_done()

    assert report.applied == []
    assert report.skipped[0]["reason"] == "backup_failed"
    assert "Nothing was changed" in report.skipped[0]["message"]
    assert await _rows(hass) == before


async def test_clear_is_aborted_when_the_backup_cannot_be_written(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """clear_statistics refuses to delete what it could not back up."""
    from custom_components.energy_guard.statistics import async_clear_statistics

    await setup_guard(hass)
    await _seed(hass)
    before = await _rows(hass)

    blocked_parent = tmp_path / "not_a_directory"
    await hass.async_add_executor_job(blocked_parent.write_text, "file")

    result = await async_clear_statistics(
        hass,
        statistic_ids=[STAT],
        confirm=True,
        create_backup=True,
        backup_dir=blocked_parent / "backups",
    )
    await hass.async_block_till_done()

    assert result["status"] == "failed"
    assert result["cleared"] == []
    assert "nothing was cleared" in result["message"]
    assert await _rows(hass) == before
