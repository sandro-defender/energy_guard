"""Statistics scan / repair / clear tests (kWh and GEL)."""

from __future__ import annotations

import asyncio
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

from .conftest import setup_guard

ENERGY_STAT = "energy_guard_test:grid_import"
COST_STAT = "energy_guard_test:grid_import_cost"
PRICE = 0.235
FALSE_ENERGY = 38243.46
START = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(
    hours=30
)


def _metadata(statistic_id: str, unit: str, unit_class: str | None) -> dict:
    """Return metadata for an external statistic."""
    return {
        "has_mean": False,
        "mean_type": StatisticMeanType.NONE,
        "has_sum": True,
        "name": statistic_id,
        "source": statistic_id.split(":")[0],
        "statistic_id": statistic_id,
        "unit_class": unit_class,
        "unit_of_measurement": unit,
    }


@pytest.fixture(autouse=True)
def recorder(recorder_mock):
    """Run every test in this module against an in-memory recorder."""
    return recorder_mock


async def _seed_broken_statistics(hass: HomeAssistant) -> None:
    """Seed statistics with the classic reconnect corruption.

    index  0..23 : state 38243.46, sum  38243.46   meter lifetime value, no use
    index 24     : state 0.0,      sum  38243.46   the reconnect zero
    index 25     : state 38243.46, sum  76486.92   recorder counted the value again
    index 26+    : unchanged

    The false offset is therefore exactly 38243.46 kWh, and at a tariff of
    0.235 GEL/kWh the matching monetary correction is 8987.21 GEL.
    """
    energy_rows = []
    cost_rows = []
    for index in range(30):
        start = START + timedelta(hours=index)
        if index <= 23:
            state, total = FALSE_ENERGY, FALSE_ENERGY
        elif index == 24:
            state, total = 0.0, FALSE_ENERGY
        else:
            state, total = FALSE_ENERGY, FALSE_ENERGY * 2
        energy_rows.append({"start": start, "state": state, "sum": total})
        cost_rows.append({"start": start, "state": state * PRICE, "sum": total * PRICE})

    async_add_external_statistics(
        hass, _metadata(ENERGY_STAT, "kWh", "energy"), energy_rows
    )
    async_add_external_statistics(hass, _metadata(COST_STAT, "GEL", None), cost_rows)
    await get_instance(hass).async_block_till_done()
    await hass.async_block_till_done()


async def _wait_for(predicate, *, attempts: int = 10, delay: float = 0.2):
    """Poll a read-only predicate until it is true (recorder timing safe)."""
    for attempt in range(attempts):
        result = await predicate()
        if result:
            return result
        if attempt + 1 < attempts:
            await asyncio.sleep(delay)
    return await predicate()


async def _sums(hass: HomeAssistant, statistic_id: str) -> list[dict]:
    """Read the rows of a statistic."""
    rows = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        START - timedelta(hours=2),
        None,
        {statistic_id},
        "hour",
        None,
        {"sum", "state"},
    )
    return rows.get(statistic_id, [])


async def test_scan_finds_zero_then_restore(recorder_mock, hass: HomeAssistant) -> None:
    """A scan reports the false offset with evidence and a fingerprint."""
    await setup_guard(
        hass,
        cost={
            "enabled": True,
            "price": PRICE,
            "currency": "GEL",
            "energy_statistic_id": ENERGY_STAT,
            "cost_statistic_id": COST_STAT,
        },
    )
    await _seed_broken_statistics(hass)

    result = await hass.services.async_call(
        "energy_guard",
        "scan_statistics",
        {"statistic_ids": [ENERGY_STAT], "start_time": START},
        blocking=True,
        return_response=True,
    )

    assert result["read_only"] is True
    candidates = result["candidates"]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["statistic_id"] == ENERGY_STAT
    assert "zero_then_restore" in candidate["evidence"]
    assert candidate["offset"] == pytest.approx(-FALSE_ENERGY, abs=0.01)
    assert candidate["estimated_false_energy"] == pytest.approx(FALSE_ENERGY, abs=0.01)
    assert candidate["unit"] == "kWh"
    assert candidate["fingerprint"]

    # The monetary suggestion needs its own confirmation.
    suggestions = result["cost_suggestions"]
    assert len(suggestions) == 1
    assert suggestions[0]["statistic_id"] == COST_STAT
    assert suggestions[0]["offset"] == pytest.approx(-FALSE_ENERGY * PRICE, abs=0.01)
    assert suggestions[0]["unit"] == "GEL"
    assert suggestions[0]["separate_confirmation_required"] is True


async def test_repair_requires_confirmation(recorder_mock, hass: HomeAssistant) -> None:
    """Without confirm: true nothing is modified and a preview is returned."""
    await setup_guard(
        hass,
        cost={
            "enabled": True,
            "price": PRICE,
            "currency": "GEL",
            "energy_statistic_id": ENERGY_STAT,
            "cost_statistic_id": COST_STAT,
        },
    )
    await _seed_broken_statistics(hass)
    before = await _sums(hass, ENERGY_STAT)

    result = await hass.services.async_call(
        "energy_guard",
        "repair_statistics",
        {
            "repairs": [
                {
                    "statistic_id": ENERGY_STAT,
                    "start_time": (START + timedelta(hours=25)).isoformat(),
                    "offset": -FALSE_ENERGY,
                    "unit": "kWh",
                }
            ]
        },
        blocking=True,
        return_response=True,
    )

    assert result["status"] == "preview"
    assert result["applied_count"] == 0
    assert result["preview"][0]["offset"] == -FALSE_ENERGY
    assert result["preview"][0]["expected_value"] is not None
    assert result["rollback"][0]["offset"] == FALSE_ENERGY
    assert await _sums(hass, ENERGY_STAT) == before

    # ... and the monetary repair cannot be applied without confirm_cost.
    with pytest.raises(Exception) as err:
        await hass.services.async_call(
            "energy_guard",
            "repair_statistics",
            {
                "repairs": [
                    {
                        "statistic_id": ENERGY_STAT,
                        "start_time": (START + timedelta(hours=25)).isoformat(),
                        "offset": -FALSE_ENERGY,
                        "unit": "kWh",
                    }
                ],
                "cost_repairs": [
                    {
                        "statistic_id": COST_STAT,
                        "start_time": (START + timedelta(hours=25)).isoformat(),
                        "offset": -FALSE_ENERGY * PRICE,
                        "unit": "GEL",
                    }
                ],
                "confirm": True,
            },
            blocking=True,
            return_response=True,
        )
    assert "confirm_cost" in str(err.value)
    assert await _sums(hass, ENERGY_STAT) == before


async def _backup_count(directory: Path, expected: int) -> int | None:
    """Return the backup count once it reaches ``expected`` (no blocking I/O)."""
    files = await asyncio.get_running_loop().run_in_executor(
        None, lambda: list(directory.glob("*.json")) if directory.exists() else []
    )
    return len(files) if len(files) >= expected else None


async def _sums_after(
    hass: HomeAssistant, statistic_id: str, expected: float
) -> list[dict] | None:
    """Return the rows once the newest sum matches ``expected``."""
    rows = await _sums(hass, statistic_id)
    if (
        rows
        and rows[-1]["sum"] is not None
        and abs(float(rows[-1]["sum"]) - expected) <= 0.01
    ):
        return rows
    return None


async def test_repair_applies_kwh_and_gel(
    recorder_mock, hass: HomeAssistant, tmp_path: Path
) -> None:
    """A confirmed repair fixes the energy statistic and the GEL cost statistic."""
    await setup_guard(
        hass,
        cost={
            "enabled": True,
            "price": PRICE,
            "currency": "GEL",
            "energy_statistic_id": ENERGY_STAT,
            "cost_statistic_id": COST_STAT,
        },
    )
    await _seed_broken_statistics(hass)

    start_time = START + timedelta(hours=25)
    energy_before = await _sums(hass, ENERGY_STAT)
    cost_before = await _sums(hass, COST_STAT)

    result = await hass.services.async_call(
        "energy_guard",
        "repair_statistics",
        {
            "repairs": [
                {
                    "statistic_id": ENERGY_STAT,
                    "start_time": start_time.isoformat(),
                    "offset": -FALSE_ENERGY,
                    "unit": "kWh",
                }
            ],
            "cost_repairs": [
                {
                    "statistic_id": COST_STAT,
                    "start_time": start_time.isoformat(),
                    "offset": -FALSE_ENERGY * PRICE,
                    "unit": "GEL",
                }
            ],
            "confirm": True,
            "confirm_cost": True,
        },
        blocking=True,
        return_response=True,
    )

    assert result["status"] == "applied"
    assert result["applied_count"] == 2
    assert all(item["verified"] for item in result["applied"])

    energy_after = await _wait_for(
        lambda: _sums_after(hass, ENERGY_STAT, energy_before[-1]["sum"] - FALSE_ENERGY)
    )
    cost_after = await _wait_for(
        lambda: _sums_after(
            hass, COST_STAT, cost_before[-1]["sum"] - FALSE_ENERGY * PRICE
        )
    )
    assert energy_after
    assert cost_after

    # A JSON backup of every changed statistic exists.
    backup_dir = Path(hass.config.config_dir, "energy_guard", "backups")
    await _wait_for(lambda: _backup_count(backup_dir, 2))
    backups = await hass.async_add_executor_job(
        lambda: sorted(backup_dir.glob("*.json"))
    )
    assert len(backups) == 2
    payload = json.loads(backups[0].read_text())
    assert payload["energy_guard"]["checksum"].startswith("sha256:")
    assert payload["rows"]
    assert result["rollback"][0]["offset"] == FALSE_ENERGY


async def test_clear_statistics_requires_confirmation(
    recorder_mock, hass: HomeAssistant
) -> None:
    """clear_statistics never deletes anything without confirm: true."""
    await setup_guard(hass)
    await _seed_broken_statistics(hass)

    preview = await hass.services.async_call(
        "energy_guard",
        "clear_statistics",
        {"statistic_ids": [ENERGY_STAT]},
        blocking=True,
        return_response=True,
    )
    assert preview["status"] == "confirmation_required"
    assert preview["preview"][0]["rows"] > 0
    assert await _sums(hass, ENERGY_STAT)

    cleared = await hass.services.async_call(
        "energy_guard",
        "clear_statistics",
        {"statistic_ids": [ENERGY_STAT], "confirm": True},
        blocking=True,
        return_response=True,
    )
    assert cleared["status"] == "cleared"
    assert cleared["statistic_ids"] == [ENERGY_STAT]
    assert await _sums(hass, ENERGY_STAT) == []
    # Untouched: the cost statistic still exists.
    assert await _sums(hass, COST_STAT)


async def test_repair_skips_unknown_statistics(
    recorder_mock, hass: HomeAssistant
) -> None:
    """Unknown statistic ids are reported, never created or guessed."""
    await setup_guard(hass)
    await _seed_broken_statistics(hass)

    result = await hass.services.async_call(
        "energy_guard",
        "repair_statistics",
        {
            "repairs": [
                {
                    "statistic_id": "sensor.does_not_exist",
                    "start_time": START.isoformat(),
                    "offset": -1.0,
                    "unit": "kWh",
                }
            ],
            "confirm": True,
        },
        blocking=True,
        return_response=True,
    )
    assert result["status"] == "failed"
    assert result["skipped"][0]["reason"] == "unknown_statistic_id"
    assert result["applied_count"] == 0
