"""Utility meter calibration tests.

Covers the last mandated scenario: ``energy_guard.calibrate_utility_meter``
with an exact value and computed from a protected source + a cycle start
baseline (either supplied directly or read from the recorded history).

Everything is read-only until ``confirm: true`` is passed.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.energy_guard.hub import get_hub

from .conftest import SOURCE, protected_definition, set_source, setup_guard

METER = "sensor.grid_import_monthly"
PROTECTED = "sensor.grid_import_protected"
LIFETIME = 38243.46


@pytest.fixture(autouse=True)
def recorder(recorder_mock):
    """Run every test in this module against an in-memory recorder."""
    return recorder_mock


@pytest.fixture
def expected_lingering_timers() -> bool:
    """The utility_meter schedules a timer for its next cycle boundary."""
    return True


async def _setup(hass: HomeAssistant, value: float = LIFETIME) -> None:
    """Set up a protected sensor plus a monthly utility meter fed by it."""
    set_source(hass, SOURCE, value - 1.0)
    await setup_guard(hass, protected=[protected_definition()])
    assert await async_setup_component(
        hass,
        "utility_meter",
        {
            "utility_meter": {
                "grid_import_monthly": {
                    "source": PROTECTED,
                    "cycle": "monthly",
                    "name": "Grid import monthly",
                }
            }
        },
    )
    await hass.async_block_till_done()
    # The meter needs one source update to pick up the unit and start counting.
    set_source(hass, SOURCE, value)
    await hass.async_block_till_done()


async def _seed_source_history(hass: HomeAssistant, when, value: float) -> None:
    """Seed a recorded hourly value for the protected source."""
    async_import_statistics(
        hass,
        {
            "has_mean": False,
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": PROTECTED,
            "source": "recorder",
            "statistic_id": PROTECTED,
            "unit_class": "energy",
            "unit_of_measurement": "kWh",
        },
        [{"start": when, "state": value, "sum": value}],
    )
    await get_instance(hass).async_block_till_done()
    await hass.async_block_till_done()


async def _calibrate(hass: HomeAssistant, **data) -> dict:
    """Call the calibration service and return its response."""
    return await hass.services.async_call(
        "energy_guard",
        "calibrate_utility_meter",
        data,
        blocking=True,
        return_response=True,
    )


async def test_utility_meter_exists_and_starts_at_source(hass: HomeAssistant) -> None:
    """The meter under test really is a utility_meter entity."""
    await _setup(hass)
    meter = hass.states.get(METER)
    assert meter is not None
    assert meter.attributes["unit_of_measurement"] == "kWh"
    assert meter.state not in ("unknown", "unavailable")


async def test_calibrate_exact_value_requires_confirmation(
    hass: HomeAssistant,
) -> None:
    """An exact value is only applied with confirm: true."""
    await _setup(hass)

    before = hass.states.get(METER).state

    preview = await _calibrate(hass, entity_id=METER, value=123.456)
    assert preview["status"] == "preview"
    assert preview["applied"] is False
    assert preview["target_value"] == 123.456
    assert preview["current_value"] == pytest.approx(float(before))
    # Preview really changed nothing.
    assert hass.states.get(METER).state == before

    applied = await _calibrate(hass, entity_id=METER, value=123.456, confirm=True)
    assert applied["status"] == "calibrated"
    assert applied["applied"] is True
    assert applied["verified"] is True
    assert applied["new_value"] == pytest.approx(123.456, abs=0.01)
    assert float(hass.states.get(METER).state) == pytest.approx(123.456, abs=0.01)

    # The calibration is part of the audit trail.
    hub = get_hub(hass)
    assert hub.calibrations[-1]["entity_id"] == METER
    assert hub.calibrations[-1]["target_value"] == 123.456
    assert hub.calibrations[-1]["source_entity_id"] is None


async def test_calibrate_from_protected_source_and_baseline(
    hass: HomeAssistant,
) -> None:
    """The target is computed as protected cumulative value minus baseline."""
    await _setup(hass)

    result = await _calibrate(
        hass,
        entity_id=METER,
        source_entity_id=PROTECTED,
        baseline_value=38000.0,
        cycle_start=dt_util.utcnow().replace(minute=0, second=0, microsecond=0),
        confirm=True,
    )

    assert result["source_value"] == pytest.approx(LIFETIME)
    assert result["baseline_value"] == pytest.approx(38000.0)
    assert result["target_value"] == pytest.approx(243.46)
    assert result["delta"] == pytest.approx(243.46 - float(result["current_value"]))
    assert result["status"] == "calibrated"
    assert float(hass.states.get(METER).state) == pytest.approx(243.46, abs=0.01)

    hub = get_hub(hass)
    assert hub.calibrations[-1]["source_entity_id"] == PROTECTED
    assert hub.calibrations[-1]["baseline_value"] == pytest.approx(38000.0)


async def test_calibrate_baseline_read_from_history(hass: HomeAssistant) -> None:
    """Without baseline_value the cycle start value is read from the recorder."""
    await _setup(hass)

    cycle_start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    await _seed_source_history(hass, cycle_start - timedelta(hours=2), 38000.0)
    await _seed_source_history(hass, cycle_start - timedelta(hours=1), 38200.0)

    result = await _calibrate(
        hass,
        entity_id=METER,
        source_entity_id=PROTECTED,
        cycle_start=cycle_start,
        confirm=True,
    )

    assert result["baseline_value"] == pytest.approx(38200.0)
    assert result["target_value"] == pytest.approx(LIFETIME - 38200.0)
    assert float(hass.states.get(METER).state) == pytest.approx(43.46, abs=0.01)


async def test_calibrate_skips_when_already_correct(hass: HomeAssistant) -> None:
    """A no-op calibration is reported instead of calling utility_meter."""
    await _setup(hass)
    await _calibrate(hass, entity_id=METER, value=42.0, confirm=True)

    again = await _calibrate(hass, entity_id=METER, value=42.0, confirm=True)
    assert again["status"] == "already_correct"
    assert again["applied"] is False
    assert float(hass.states.get(METER).state) == pytest.approx(42.0, abs=0.01)


async def test_calibrate_rejects_non_utility_meter_entity(
    hass: HomeAssistant,
) -> None:
    """Only utility_meter entities may be calibrated through Energy Guard."""
    await _setup(hass)

    with pytest.raises(ServiceValidationError):
        await _calibrate(hass, entity_id=PROTECTED, value=1.0, confirm=True)


async def test_calibrate_requires_a_value_or_a_source(hass: HomeAssistant) -> None:
    """Neither value nor source_entity_id is a user error, not a crash."""
    await _setup(hass)

    with pytest.raises(ServiceValidationError):
        await _calibrate(hass, entity_id=METER, confirm=True)
