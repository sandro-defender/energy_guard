"""Detection tests: the scanner is read-only and reports what it can prove."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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

from custom_components.energy_guard.detection import (
    detect_offsets,
    fingerprint,
    median_positive,
)
from custom_components.energy_guard.models import DetectionRules

from .conftest import setup_guard

START = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(
    hours=10
)
STAT = "energy_guard_test:detect"


@pytest.fixture(autouse=True)
def recorder(recorder_mock):
    """Run every test in this module against an in-memory recorder."""
    return recorder_mock


def _rows(pairs: list[tuple[float, float]]) -> list[dict]:
    """Return statistics rows from ``(state, sum)`` pairs."""
    return [
        {"start": START + timedelta(hours=index), "state": state, "sum": total}
        for index, (state, total) in enumerate(pairs)
    ]


def test_median_positive_ignores_zero_and_negative_increases() -> None:
    """The baseline increase is the median of the real (positive) increases."""
    assert median_positive([0.0, 0.0, 1.0, 1.0, 1.0]) == 1.0
    assert median_positive([]) is None
    assert median_positive([0.0, -5.0]) is None


def test_detect_zero_then_restore_is_read_only() -> None:
    """The reconnect signature is reported with evidence and an offset."""
    rows = _rows(
        [(38243.46, 38243.46)] * 4
        + [(0.0, 38243.46), (38243.46, 76486.92), (38243.46, 76486.92)]
    )
    candidates = detect_offsets(
        rows,
        statistic_id="sensor.grid_import",
        unit="kWh",
        detection=DetectionRules(),
        now=dt_util.utcnow(),
    )

    assert candidates, "the false offset must be detected"
    candidate = candidates[0]
    assert candidate.offset == pytest.approx(-38243.46)
    assert candidate.unit == "kWh"
    assert candidate.start_time == rows[5]["start"]
    assert candidate.evidence
    assert candidate.fingerprint == fingerprint(
        "sensor.grid_import", candidate.start_time, candidate.offset, "kWh"
    )
    # detect_offsets never touches its input.
    assert rows[0] == {"start": START, "state": 38243.46, "sum": 38243.46}


def test_detect_ignores_smooth_growth() -> None:
    """Normal consumption produces no candidates (no false positives)."""
    rows = _rows([(100.0 * index, 100.0 * index) for index in range(1, 12)])
    assert (
        detect_offsets(
            rows,
            statistic_id="sensor.grid_import",
            unit="kWh",
            detection=DetectionRules(),
            now=dt_util.utcnow(),
        )
        == []
    )


def test_detect_respects_the_jump_thresholds() -> None:
    """A larger threshold can silence a small offset."""
    rows = _rows([(100.0, 100.0), (110.0, 110.0), (110.0, 400.0), (120.0, 410.0)])
    strict = detect_offsets(
        rows,
        statistic_id="sensor.grid_import",
        unit="kWh",
        detection=DetectionRules(statistics_jump_threshold=10.0),
        now=dt_util.utcnow(),
    )
    lenient = detect_offsets(
        rows,
        statistic_id="sensor.grid_import",
        unit="kWh",
        detection=DetectionRules(statistics_jump_threshold=1_000_000.0),
        now=dt_util.utcnow(),
    )
    assert strict
    assert lenient == []


async def _seed(hass: HomeAssistant) -> None:
    """Seed a statistic with the reconnect corruption."""
    rows = []
    for index in range(12):
        if index < 8:
            state = total = 38243.46
        elif index == 8:
            state, total = 0.0, 38243.46
        else:
            state, total = 38243.46, 38243.46 * 2
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


async def _sums(hass: HomeAssistant) -> list[dict]:
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


async def test_scan_service_does_not_modify_anything(hass: HomeAssistant) -> None:
    """scan_statistics is read-only: same rows, no backups, no repairs."""
    await setup_guard(hass)
    await _seed(hass)
    before = await _sums(hass)

    result = await hass.services.async_call(
        "energy_guard",
        "scan_statistics",
        {"statistic_ids": [STAT], "start_time": START},
        blocking=True,
        return_response=True,
    )

    assert result["read_only"] is True
    assert result["candidate_count"] >= 1
    assert await _sums(hass) == before
    backup_dir = Path(hass.config.config_dir, "energy_guard", "backups")
    leftovers = await hass.async_add_executor_job(
        lambda: sorted(backup_dir.glob("*.json"))
    )
    assert leftovers == []

    from custom_components.energy_guard.hub import get_hub

    hub = get_hub(hass)
    assert list(hub.repairs) == []
    assert list(hub.calibrations) == []


async def test_scan_truncates_a_range_longer_than_a_year(hass: HomeAssistant) -> None:
    """An absurd range is capped and explained instead of hammering the recorder."""
    await setup_guard(hass)
    result = await hass.services.async_call(
        "energy_guard",
        "scan_statistics",
        {
            "statistic_ids": [STAT],
            "start_time": (dt_util.utcnow() - timedelta(days=2000)).isoformat(),
        },
        blocking=True,
        return_response=True,
    )
    assert any("longer than one year" in warning for warning in result["warnings"])


async def test_scan_warns_about_unknown_statistics(hass: HomeAssistant) -> None:
    """A statistic id without long term statistics is reported, not fatal."""
    await setup_guard(hass)
    result = await hass.services.async_call(
        "energy_guard",
        "scan_statistics",
        {"statistic_ids": ["sensor.does_not_exist"]},
        blocking=True,
        return_response=True,
    )
    assert result["candidate_count"] == 0
    assert any("no statistics" in warning for warning in result["warnings"])


async def test_scan_without_recorder_reports_cleanly(hass: HomeAssistant) -> None:
    """No recorder: the scan explains itself instead of raising."""
    await setup_guard(hass)
    result = await hass.services.async_call(
        "energy_guard",
        "scan_statistics",
        {"statistic_ids": ["sensor.grid_import"]},
        blocking=True,
        return_response=True,
    )
    assert result["status"] in ("ok", "recorder_unavailable")
    assert result["read_only"] is True


def test_fingerprint_is_stable_and_timezone_aware() -> None:
    """The fingerprint identifies one offset, independent of the local timezone."""
    moment = datetime(2026, 9, 18, 4, 0, tzinfo=UTC)
    same = moment.astimezone(dt_util.get_default_time_zone())
    assert fingerprint("sensor.x", moment, -1.5, "kWh") == fingerprint(
        "sensor.x", same, -1.5, "kWh"
    )
    assert fingerprint("sensor.x", moment, -1.5, "kWh") != fingerprint(
        "sensor.x", moment, -1.6, "kWh"
    )
