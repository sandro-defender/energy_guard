"""Scan-scope tests: "scan everything" finds corruption Energy Guard is not linked to.

The scanner must stay cheap by default (``scope: linked``) while still offering
an explicit opt-in that covers statistics Energy Guard does not manage:

* ``energy`` - every cumulative statistic that carries an energy unit,
* ``all``    - every cumulative statistic in the recorder,

and explicit ``statistic_ids``/``entity_ids`` always win over the scope.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.energy_guard.const import (
    DEFAULT_SCAN_SCOPE,
    SCAN_SCOPE_ALL,
    SCAN_SCOPE_ENERGY,
    SCAN_SCOPE_EXPLICIT,
    SCAN_SCOPE_LINKED,
    SCAN_SCOPES,
)
from custom_components.energy_guard.detection import async_discover_statistic_ids
from custom_components.energy_guard.hub import get_hub
from custom_components.energy_guard.models import DetectionRules

from .conftest import SOURCE, protected_definition, set_source, setup_guard

UNMANAGED_ENERGY = "other_test:garage_meter"
UNMANAGED_WATER = "other_test:water_meter"
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


async def _seed_corrupted_statistics(
    hass: HomeAssistant, statistic_id: str, unit: str, unit_class: str | None
) -> None:
    """Seed one statistic with the classic zero-then-restore corruption."""
    rows = []
    for index in range(30):
        start = START + timedelta(hours=index)
        if index <= 23:
            state = total = FALSE_ENERGY
        elif index == 24:
            state, total = 0.0, FALSE_ENERGY
        else:
            state, total = FALSE_ENERGY, FALSE_ENERGY * 2
        rows.append({"start": start, "state": state, "sum": total})
    async_add_external_statistics(hass, _metadata(statistic_id, unit, unit_class), rows)
    await get_instance(hass).async_block_till_done()
    await hass.async_block_till_done()


async def _seed_clean_statistics(
    hass: HomeAssistant, statistic_id: str, unit: str, unit_class: str | None
) -> None:
    """Seed one healthy cumulative statistic (1 kWh per hour, no anomalies)."""
    rows = [
        {"start": START + timedelta(hours=index), "state": index, "sum": float(index)}
        for index in range(30)
    ]
    async_add_external_statistics(hass, _metadata(statistic_id, unit, unit_class), rows)
    await get_instance(hass).async_block_till_done()
    await hass.async_block_till_done()


async def _scan(hass: HomeAssistant, **data) -> dict:
    """Call the scan service and return the response."""
    payload = {"start_time": START, **data}
    return await hass.services.async_call(
        "energy_guard", "scan_statistics", payload, blocking=True, return_response=True
    )


def _candidate_ids(result: dict) -> set[str]:
    """Return the statistic ids of the candidates of a scan response."""
    return {candidate["statistic_id"] for candidate in result["candidates"]}


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
async def test_discover_linked_scope_returns_the_configured_statistics(
    hass: HomeAssistant,
) -> None:
    """The default scope returns only what Energy Guard is linked to."""
    set_source(hass, SOURCE, 38243.46)
    await setup_guard(hass, protected=[protected_definition()])
    hub = get_hub(hass, hass.config_entries.async_entries("energy_guard")[0].entry_id)

    ids, warnings = await async_discover_statistic_ids(
        hass, scope=SCAN_SCOPE_LINKED, linked_ids=hub.statistic_ids()
    )

    assert SOURCE in ids
    assert all(statistic_id.startswith("sensor.") for statistic_id in ids)
    assert warnings == []
    assert DEFAULT_SCAN_SCOPE == SCAN_SCOPE_LINKED
    assert SCAN_SCOPES == (SCAN_SCOPE_LINKED, SCAN_SCOPE_ENERGY, SCAN_SCOPE_ALL)


async def test_discover_all_scope_lists_every_cumulative_statistic(
    hass: HomeAssistant,
) -> None:
    """`all` finds the unmanaged energy and water statistics, `energy` only energy."""
    await setup_guard(hass)
    await _seed_corrupted_statistics(hass, UNMANAGED_ENERGY, "kWh", "energy")
    await _seed_corrupted_statistics(hass, UNMANAGED_WATER, "m³", "volume")

    energy_ids, _ = await async_discover_statistic_ids(hass, scope=SCAN_SCOPE_ENERGY)
    all_ids, _ = await async_discover_statistic_ids(hass, scope=SCAN_SCOPE_ALL)

    assert UNMANAGED_ENERGY in energy_ids
    assert UNMANAGED_WATER not in energy_ids
    assert {UNMANAGED_ENERGY, UNMANAGED_WATER} <= set(all_ids)


async def test_discover_truncates_and_warns(hass: HomeAssistant) -> None:
    """A cap keeps a whole-recorder scan predictable, and it is reported."""
    await setup_guard(hass)
    await _seed_corrupted_statistics(hass, UNMANAGED_ENERGY, "kWh", "energy")
    await _seed_corrupted_statistics(hass, UNMANAGED_WATER, "m³", "volume")

    ids, warnings = await async_discover_statistic_ids(
        hass, scope=SCAN_SCOPE_ALL, linked_ids=[SOURCE], max_statistics=1
    )

    assert ids[0] == SOURCE  # linked statistics always come first
    # The cap applies to the discovered statistics only, never to the linked ones.
    assert len(ids) - 1 <= 1
    assert any("only the first 1" in warning for warning in warnings)


async def test_discover_warns_when_the_scope_is_empty(hass: HomeAssistant) -> None:
    """An empty recorder explains itself instead of failing silently."""
    await setup_guard(hass)

    ids, warnings = await async_discover_statistic_ids(hass, scope=SCAN_SCOPE_ALL)

    assert ids == []
    assert any("No statistics were found" in warning for warning in warnings)


# ---------------------------------------------------------------------------
# the service
# ---------------------------------------------------------------------------
async def test_default_scan_ignores_unmanaged_sensors(hass: HomeAssistant) -> None:
    """A default (linked) scan does not touch statistics Energy Guard is not linked to."""
    set_source(hass, SOURCE, 38243.46)
    await setup_guard(hass, protected=[protected_definition()])
    await _seed_corrupted_statistics(hass, UNMANAGED_ENERGY, "kWh", "energy")

    result = await _scan(hass)

    assert result["scope"] == SCAN_SCOPE_LINKED
    assert result["candidates"] == []
    # The response documents exactly what was read: only the linked sensors.
    assert set(result["statistic_ids"]) == {SOURCE, "sensor.grid_import_protected"}
    assert result["statistic_count"] == 2
    assert UNMANAGED_ENERGY not in result["statistic_ids"]


async def test_energy_scope_finds_unmanaged_energy_sensors(hass: HomeAssistant) -> None:
    """`scope: energy` reports the corruption of a sensor Energy Guard does not manage."""
    await setup_guard(hass)
    await _seed_corrupted_statistics(hass, UNMANAGED_ENERGY, "kWh", "energy")
    await _seed_corrupted_statistics(hass, UNMANAGED_WATER, "m³", "volume")

    result = await _scan(hass, scope=SCAN_SCOPE_ENERGY)

    assert result["scope"] == SCAN_SCOPE_ENERGY
    assert result["read_only"] is True
    assert _candidate_ids(result) == {UNMANAGED_ENERGY}
    candidate = result["candidates"][0]
    assert "zero_then_restore" in candidate["evidence"]
    assert candidate["offset"] == pytest.approx(-FALSE_ENERGY, abs=0.01)

    # The scope of the last scan is visible on the diagnostic sensor.
    await hass.async_block_till_done()
    attributes = hass.states.get("sensor.energy_guard_statistics_issues").attributes
    assert attributes["last_scan_scope"] == SCAN_SCOPE_ENERGY
    assert attributes["last_scan_statistic_count"] == result["statistic_count"]
    assert attributes["scan_scope"] == DEFAULT_SCAN_SCOPE


async def test_all_scope_finds_non_energy_statistics(hass: HomeAssistant) -> None:
    """`scope: all` also checks cumulative statistics without an energy unit."""
    await setup_guard(hass)
    await _seed_corrupted_statistics(hass, UNMANAGED_WATER, "m³", "volume")
    await _seed_clean_statistics(hass, "other_test:clean_energy", "kWh", "energy")

    energy_result = await _scan(hass, scope=SCAN_SCOPE_ENERGY)
    all_result = await _scan(hass, scope=SCAN_SCOPE_ALL)

    assert energy_result["candidates"] == []
    assert UNMANAGED_WATER not in energy_result["statistic_ids"]
    assert _candidate_ids(all_result) == {UNMANAGED_WATER}


async def test_configured_scope_is_the_default_of_every_scan(
    hass: HomeAssistant,
) -> None:
    """The options-flow scan scope is used when the service call has none."""
    await setup_guard(
        hass, detection={**DetectionRules().to_dict(), "scan_scope": SCAN_SCOPE_ALL}
    )
    await _seed_corrupted_statistics(hass, UNMANAGED_WATER, "m³", "volume")

    result = await _scan(hass)

    assert result["scope"] == SCAN_SCOPE_ALL
    assert _candidate_ids(result) == {UNMANAGED_WATER}


async def test_explicit_ids_win_over_the_scope(hass: HomeAssistant) -> None:
    """Passing ids keeps the historic behaviour, whatever the scope says."""
    await setup_guard(hass)
    await _seed_corrupted_statistics(hass, UNMANAGED_ENERGY, "kWh", "energy")

    result = await _scan(hass, scope=SCAN_SCOPE_ALL, statistic_ids=[UNMANAGED_ENERGY])

    assert result["scope"] == SCAN_SCOPE_EXPLICIT
    assert result["statistic_ids"] == [UNMANAGED_ENERGY]
    assert _candidate_ids(result) == {UNMANAGED_ENERGY}


async def test_scanning_is_still_read_only_with_a_wide_scope(
    hass: HomeAssistant,
) -> None:
    """A wide scan reads statistics, never writes them."""
    await setup_guard(hass)
    await _seed_corrupted_statistics(hass, UNMANAGED_ENERGY, "kWh", "energy")
    before = await _rows(hass, UNMANAGED_ENERGY)

    await _scan(hass, scope=SCAN_SCOPE_ALL)

    assert await _rows(hass, UNMANAGED_ENERGY) == before


async def _rows(hass: HomeAssistant, statistic_id: str) -> list[dict]:
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


async def test_wide_scope_scan_is_reported_for_every_entry(hass: HomeAssistant) -> None:
    """A single entry returns a flat response, and the hub remembers the scan."""
    await setup_guard(hass)
    await _seed_clean_statistics(hass, "other_test:clean_energy", "kWh", "energy")
    entry = hass.config_entries.async_entries("energy_guard")[0]
    hub = get_hub(hass, entry.entry_id)
    # A second entry is refused (single_config_entry), so the shape of the
    # response is covered by the single entry: a flat dict with the scope.
    assert hub.last_scan_scope is None
    result = await _scan(hass, scope=SCAN_SCOPE_ALL)

    assert isinstance(result, dict)
    assert "config_entries" not in result
    assert result["scope"] == SCAN_SCOPE_ALL
    await asyncio.sleep(0)
    assert hub.last_scan_scope == SCAN_SCOPE_ALL
    assert hub.last_scan_statistic_count == result["statistic_count"]
