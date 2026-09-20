"""Configuration normalization and migration tests (backwards compatibility).

Energy Guard keeps every setting in the config entry options.  These tests pin
the promise that a configuration written by an older release - or damaged by a
manual edit - still loads, keeps working entity ids and is repaired on the fly.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.energy_guard.const import (
    CONF_BACKUPS,
    CONF_COST,
    CONF_DERIVED,
    CONF_DETECTION,
    CONF_PROTECTED,
    CONF_UTILITY_METERS,
    DOMAIN,
)
from custom_components.energy_guard.hub import get_hub
from custom_components.energy_guard.migrations import (
    CURRENT_VERSION,
    async_migrate_entry,
    needs_migration,
    normalize_options,
)
from custom_components.energy_guard.models import SensorDefinition

from .conftest import SOURCE, make_entry, protected_definition, set_source

#: A configuration as an older release (v1) wrote it: only the sections the
#: config flow knew about, one definition without the newer keys.
LEGACY_OPTIONS = {
    CONF_PROTECTED: [
        {
            "id": "test_grid_import",
            "name": "Grid import protected",
            "source_entity_id": SOURCE,
            "unit_of_measurement": "kWh",
            "offset": 0.0,
        }
    ],
    CONF_DERIVED: [],
    CONF_DETECTION: {"scan_interval": 600},
    "some_future_key": {"kept": True},
}


def test_normalize_empty_options_yields_all_sections() -> None:
    """An empty payload becomes the documented default configuration."""
    options, notes = normalize_options({})

    assert notes == []
    for section in (
        CONF_PROTECTED,
        CONF_DERIVED,
        CONF_UTILITY_METERS,
        CONF_DETECTION,
        CONF_COST,
        CONF_BACKUPS,
    ):
        assert section in options
    assert options[CONF_PROTECTED] == []
    assert options[CONF_DETECTION]["lookback_hours"] == 24


def test_normalize_fills_defaults_and_keeps_unknown_keys() -> None:
    """Missing definition keys get defaults; unknown keys are never dropped."""
    options, _ = normalize_options(LEGACY_OPTIONS)

    definition = options[CONF_PROTECTED][0]
    assert definition["source_entity_id"] == SOURCE
    assert definition["zero_min_previous"] == 1.0
    assert definition["confirm_scans"] == 2
    assert definition["do_not_decrease"] is True
    # The payload is normalized to the full definition shape: every key the
    # models know exists, and nothing else (unknown keys are dropped here, the
    # top level payload keeps them).
    assert set(protected_definition()) <= set(definition)
    assert set(definition) == set(SensorDefinition(id="", name="").to_dict())
    assert options[CONF_DETECTION]["scan_interval"] == 600
    assert options[CONF_DETECTION]["lookback_hours"] == 24
    assert options["some_future_key"] == {"kept": True}


def test_normalize_is_idempotent() -> None:
    """Running the normalization twice never changes anything."""
    once, _ = normalize_options(LEGACY_OPTIONS)
    twice, notes = normalize_options(once)

    assert once == twice
    assert notes == []
    assert needs_migration(once) is False
    assert needs_migration(LEGACY_OPTIONS) is True


def test_normalize_rejects_unusable_entries() -> None:
    """Entries that cannot be matched to an entity are dropped or disabled."""
    options, notes = normalize_options(
        {
            CONF_PROTECTED: [{"name": "no id"}],
            CONF_UTILITY_METERS: [
                {
                    "id": "meter",
                    "name": "Meter",
                    "utility_meter_entity_id": "input_number.x",
                }
            ],
            CONF_DETECTION: "not a dict",
            CONF_COST: {"enabled": True, "price": 0.235},
        }
    )

    assert options[CONF_PROTECTED] == []
    assert options[CONF_UTILITY_METERS][0]["enabled"] is False
    assert options[CONF_DETECTION]["scan_interval"] == 300
    assert options[CONF_COST]["enabled"] is False
    assert any("without an id" in note for note in notes)
    assert any("no valid utility_meter entity id" in note for note in notes)
    assert any("Cost repair was enabled" in note for note in notes)


async def test_migrate_entry_upgrades_a_legacy_payload(hass: HomeAssistant) -> None:
    """A version 1 entry is rewritten and persisted at the current version."""
    entry = make_entry(version=1, **LEGACY_OPTIONS)
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    await hass.async_block_till_done()

    assert entry.version == CURRENT_VERSION
    assert entry.options[CONF_DETECTION]["lookback_hours"] == 24
    assert entry.options["some_future_key"] == {"kept": True}
    assert needs_migration(entry.options) is False


async def test_migrate_entry_is_a_noop_when_current(hass: HomeAssistant) -> None:
    """A current entry is left exactly as it is."""
    options, _ = normalize_options(LEGACY_OPTIONS)
    entry = make_entry(version=CURRENT_VERSION, **options)
    entry.add_to_hass(hass)

    before = dict(entry.options)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options == before


async def test_migrate_entry_refuses_a_newer_version(hass: HomeAssistant) -> None:
    """A payload from a newer release is refused instead of being downgraded."""
    entry = make_entry(version=CURRENT_VERSION + 1, protected_sensors=[])
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is False
    assert entry.version == CURRENT_VERSION + 1


async def test_legacy_entry_still_sets_up_and_keeps_its_entity_id(
    hass: HomeAssistant,
) -> None:
    """The migrated entry protects sensors and keeps the documented entity id.

    This is the backwards compatibility promise: a user who installed Energy
    Guard 1.0 (sparse options, version 1) gets the same entity ids after the
    upgrade - ``sensor.grid_import_protected`` - even though the option payload
    was rewritten.
    """
    set_source(hass, SOURCE, 38243.46)
    entry = make_entry(version=1, **LEGACY_OPTIONS)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == CURRENT_VERSION
    protected = hass.states.get("sensor.grid_import_protected")
    assert protected is not None
    assert protected.state == "38243.46"
    assert protected.attributes["device_class"] == "energy"

    registry_entry = er.async_get(hass).async_get("sensor.grid_import_protected")
    assert registry_entry is not None
    assert registry_entry.unique_id == "energy_guard_test_grid_import"

    hub = get_hub(hass)
    assert hub.config.protected[0].id == "test_grid_import"
    assert hub.config.detection.scan_interval == 600

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert DOMAIN in hass.data
