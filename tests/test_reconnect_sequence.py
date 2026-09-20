"""Entity level tests: reconnect, false reset and simultaneous failures."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as entity_registry_module
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.energy_guard.hub import get_hub

from .conftest import (
    SOURCE,
    capture_states,
    protected_definition,
    set_source,
    setup_guard,
)

PROTECTED = "sensor.grid_import_protected"
entity_registry = entity_registry_module


async def test_unavailable_to_zero_to_restored_value(hass: HomeAssistant) -> None:
    """The reconnect sequence never publishes a false 0.0."""
    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(hass, protected=[protected_definition()])

    protected = hass.states.get(PROTECTED)
    assert protected is not None
    assert protected.state == "38243.46"
    assert protected.attributes["state_class"] == "total_increasing"
    assert protected.attributes["unit_of_measurement"] == "kWh"
    # The Energy Dashboard reads the device class from the state attributes
    # (components/energy/validate.py:234), so this is the check that matters.
    assert protected.attributes["device_class"] == "energy"

    registry_entry = entity_registry.async_get(hass).async_get(PROTECTED)
    assert registry_entry is not None
    assert registry_entry.original_device_class == "energy"
    assert registry_entry.capabilities["state_class"] == "total_increasing"

    recorder = capture_states(hass, PROTECTED)

    # 1. Device reconnects and reports unavailable.
    set_source(hass, SOURCE, "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get(PROTECTED).state == "unavailable"

    # 2. Device comes back with a false 0 -> must be held, not published.
    set_source(hass, SOURCE, 0.0)
    await hass.async_block_till_done()
    assert hass.states.get(PROTECTED).state == "unavailable"

    # 3. Device reports its previous lifetime value again.
    set_source(hass, SOURCE, 38243.96)
    await hass.async_block_till_done()
    state = hass.states.get(PROTECTED)
    assert state.state == "38243.96"

    # The recorder never saw a valid 0 (which would restart the sum).
    assert "0.0" not in recorder
    assert recorder.numeric == [38243.96]
    recorder.stop()

    # The diagnostic entities explain what happened.
    last_anomaly = hass.states.get("sensor.energy_guard_last_anomaly")
    assert last_anomaly is not None
    assert last_anomaly.state == "zero_reset_blocked"
    assert last_anomaly.attributes["previous_value"] == pytest.approx(38243.46)
    assert last_anomaly.attributes["invalid_value"] == pytest.approx(0.0)
    assert last_anomaly.attributes["estimated_false_energy"] == pytest.approx(38243.46)

    issue = hass.states.get("binary_sensor.energy_guard_data_issue")
    assert issue is not None
    assert issue.state == "on"
    assert issue.attributes["blocked_events"] >= 1
    assert SOURCE in issue.attributes["affected_entities"]

    hub = get_hub(hass, entry.entry_id)
    kinds = [event.kind for event in hub.events]
    assert "source_invalid" in kinds
    assert "zero_reset_blocked" in kinds


async def test_false_cumulative_reset_is_blocked(hass: HomeAssistant) -> None:
    """A counter that drops to zero and returns is protected."""
    set_source(hass, SOURCE, 5000.0)
    await setup_guard(hass, protected=[protected_definition()])

    recorder = capture_states(hass, PROTECTED)
    set_source(hass, SOURCE, 0.0)
    await hass.async_block_till_done()
    assert hass.states.get(PROTECTED).state == "unavailable"

    set_source(hass, SOURCE, 5000.5)
    await hass.async_block_till_done()
    assert hass.states.get(PROTECTED).state == "5000.5"
    assert 0.0 not in recorder.numeric
    recorder.stop()

    # The protected sensor keeps its attributes useful for dashboards.
    assert hass.states.get(PROTECTED).attributes["guard_status"] == "ok"
    assert hass.states.get(PROTECTED).attributes["last_good_value"] == pytest.approx(
        5000.5
    )


@pytest.mark.parametrize(
    "bad_state", ["unknown", "unavailable", "", "None", "null", "nan", "error", "1,5"]
)
async def test_non_numeric_source_states_are_unavailable(
    hass: HomeAssistant, bad_state: str
) -> None:
    """A source that is not a number never becomes a number for the dashboard.

    ``nan``/``error``/``1,5`` are the classic template-sensor mistakes: they look
    numeric to a human but not to ``float()``.
    """
    set_source(hass, SOURCE, 38243.46)
    await setup_guard(hass, protected=[protected_definition()])

    recorder = capture_states(hass, PROTECTED)
    set_source(hass, SOURCE, bad_state)
    await hass.async_block_till_done()

    assert hass.states.get(PROTECTED).state == "unavailable"
    assert recorder.numeric == []  # nothing numeric reached the recorder

    # Home Assistant only keeps the core attributes on an unavailable state, so
    # the explanation is read from the diagnostic entities instead.
    hub = get_hub(hass)
    invalid = [event for event in hub.events if event.kind == "source_invalid"]
    assert invalid, [event.kind for event in hub.events]
    assert invalid[-1].details["guard_status"] == "source_invalid"
    assert invalid[-1].previous_value == pytest.approx(38243.46)
    assert invalid[-1].details["recommended_action"]
    # A held reading is an informational event: no false energy was published,
    # so the data issue sensor is not raised.
    assert hass.states.get("binary_sensor.energy_guard_data_issue").state == "off"

    # The source recovers with its real lifetime value.
    set_source(hass, SOURCE, 38243.96)
    await hass.async_block_till_done()
    state = hass.states.get(PROTECTED)
    assert state.state == "38243.96"
    assert state.attributes["guard_status"] == "ok"
    assert state.attributes["last_good_value"] == pytest.approx(38243.96)
    assert recorder.numeric == [38243.96]
    recorder.stop()


async def test_decrease_is_held_back_without_publishing_a_lower_value(
    hass: HomeAssistant,
) -> None:
    """A cumulative meter that reports a lower value is not believed instantly."""
    set_source(hass, SOURCE, 38243.46)
    await setup_guard(
        hass,
        protected=[
            protected_definition(
                do_not_decrease=True,
                zero_min_previous=1.0,
                confirm_scans=2,
                recovery_hold_scans=1,
            )
        ],
    )

    recorder = capture_states(hass, PROTECTED)
    # A smaller but non-zero value (a meter that was swapped or mis-read).
    set_source(hass, SOURCE, 12000.0)
    await hass.async_block_till_done()

    # A decreasing value is never published: the sensor goes unavailable until
    # the meter reports a value that is >= the last good reading again.
    assert hass.states.get(PROTECTED).state == "unavailable"
    assert 12000.0 not in recorder.numeric

    hub = get_hub(hass)
    blocked = [event for event in hub.events if event.kind == "decrease_blocked"]
    assert blocked, [event.kind for event in hub.events]
    assert blocked[-1].previous_value == pytest.approx(38243.46)
    assert blocked[-1].invalid_value == pytest.approx(12000.0)

    # The real value comes back: the protected sensor follows it.
    set_source(hass, SOURCE, 38243.96)
    await hass.async_block_till_done()
    assert hass.states.get(PROTECTED).state == "38243.96"
    assert 12000.0 not in recorder.numeric
    recorder.stop()


async def test_disabled_definition_creates_no_entity(hass: HomeAssistant) -> None:
    """Disabling a protected sensor in the options removes its entity."""
    set_source(hass, SOURCE, 38243.46)
    await setup_guard(hass, protected=[protected_definition(enabled=False)])

    assert hass.states.get(PROTECTED) is None
    # The dashboard entities still exist so the integration stays observable.
    assert hass.states.get("sensor.energy_guard_last_anomaly") is not None


async def test_confirmed_meter_reset_is_accepted_after_recheck(
    hass: HomeAssistant,
) -> None:
    """With accept_real_reset the reset is taken after N re-checks, not instantly."""
    set_source(hass, SOURCE, 5000.0)
    await setup_guard(
        hass,
        protected=[
            protected_definition(
                accept_real_reset=True, confirm_scans=2, name="Meter A"
            )
        ],
    )
    entity_id = "sensor.meter_a"

    set_source(hass, SOURCE, 0.0)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "0.0"


async def test_multiple_sensors_failing_simultaneously(
    hass: HomeAssistant,
) -> None:
    """Two sensors failing together are reported as a simultaneous failure."""
    other = "sensor.grid_export"
    set_source(hass, SOURCE, 1000.0)
    set_source(hass, other, 500.0)
    entry = await setup_guard(
        hass,
        protected=[
            protected_definition(),
            protected_definition(source=other, name="Grid export protected"),
        ],
    )
    assert hass.states.get("sensor.grid_export_protected").state == "500.0"

    set_source(hass, SOURCE, "unavailable")
    set_source(hass, other, "unavailable")
    await hass.async_block_till_done()
    set_source(hass, SOURCE, 0.0)
    set_source(hass, other, 0.0)
    await hass.async_block_till_done()

    assert hass.states.get(PROTECTED).state == "unavailable"
    assert hass.states.get("sensor.grid_export_protected").state == "unavailable"

    hub = get_hub(hass, entry.entry_id)
    simultaneous = [
        event for event in hub.events if event.kind == "simultaneous_anomaly"
    ]
    assert simultaneous, [event.kind for event in hub.events]
    assert set(simultaneous[-1].details["affected_entities"]) == {SOURCE, other}

    issue = hass.states.get("binary_sensor.energy_guard_data_issue")
    assert issue.state == "on"
    assert issue.attributes["simultaneous_failures"]
    assert issue.attributes["statistics_issues"] == 0
    assert issue.attributes["anomaly_count"] >= 2

    anomalies = hass.states.get("sensor.energy_guard_anomalies")
    assert anomalies.state == str(issue.attributes["anomaly_count"])
    assert anomalies.attributes["blocked_events"] == 2


async def test_derived_sum_and_phase_split(hass: HomeAssistant) -> None:
    """Derived sensors calculate sums and phase splits and apply the guard."""
    phase_a = "sensor.grid_import_phase_a"
    phase_b = "sensor.grid_import_phase_b"
    set_source(hass, SOURCE, 700.0)
    set_source(hass, phase_a, 100.0)
    set_source(hass, phase_b, 200.0)

    await setup_guard(
        hass,
        derived=[
            {
                "id": "sum_phases",
                "name": "Grid import phases sum",
                "enabled": True,
                "mode": "sum",
                "source_entity_ids": [phase_a, phase_b],
                "unit_of_measurement": "kWh",
                "do_not_decrease": True,
            },
            {
                "id": "phase_split",
                "name": "Grid import unmonitored",
                "enabled": True,
                "mode": "phase_split",
                "total_entity_id": SOURCE,
                "part_entity_ids": [phase_a, phase_b],
                "unit_of_measurement": "kWh",
                "do_not_decrease": True,
                "require_all_sources": True,
            },
        ],
    )

    assert hass.states.get("sensor.grid_import_phases_sum").state == "300.0"
    assert hass.states.get("sensor.grid_import_unmonitored").state == "400.0"

    # A missing phase holds the derived sensor (a partial sum is not published).
    set_source(hass, phase_b, "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.grid_import_unmonitored").state == "unavailable"

    set_source(hass, phase_b, 201.0)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.grid_import_unmonitored").state == "399.0"


async def test_offset_is_applied_to_the_protected_sensor(
    hass: HomeAssistant,
) -> None:
    """A configured baseline offset is added to the published value."""
    set_source(hass, SOURCE, 10.0)
    await setup_guard(
        hass, protected=[protected_definition(offset=1000.0, name="Offset meter")]
    )
    assert hass.states.get("sensor.offset_meter").state == "1010.0"


async def test_no_recorder_does_not_break_setup(hass: HomeAssistant) -> None:
    """Energy Guard works without a recorder and degrades gracefully."""
    # This test intentionally runs without the recorder autouse fixture of the
    # statistics module; the coordinator scan reports the missing recorder.
    set_source(hass, SOURCE, 10.0)
    entry = await setup_guard(hass, protected=[protected_definition()])
    assert hass.states.get(PROTECTED).state == "10.0"

    hub = get_hub(hass, entry.entry_id)
    result = hub.coordinator.data
    assert result is not None
    assert result.recorder_available is False
    assert any("recorder" in warning for warning in result.warnings)

    issues = hass.states.get("sensor.energy_guard_statistics_issues")
    assert issues is not None
    assert issues.attributes["recorder_available"] is False
