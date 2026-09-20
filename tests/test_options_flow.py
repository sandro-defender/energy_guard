"""Options flow round-trip tests for every menu section.

The options flow is the only configuration surface (Energy Guard is a
config-flow integration, no YAML), so every section is driven end to end here:
the same steps a user clicks through in *Settings -> Devices & Services ->
Energy Guard -> Configure*.  The tests assert what is stored afterwards, that
entity ids survive an edit, and that a confirmation-less delete never removes
anything.
"""

from __future__ import annotations

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.energy_guard.const import (
    CONF_BACKUPS,
    CONF_BASELINE,
    CONF_COST,
    CONF_COST_STATISTIC_ID,
    CONF_CURRENCY,
    CONF_DERIVED,
    CONF_DETECTION,
    CONF_ENABLED,
    CONF_ENERGY_STATISTIC_ID,
    CONF_ID,
    CONF_MODE,
    CONF_NAME,
    CONF_OFFSET,
    CONF_PARTS,
    CONF_PRICE,
    CONF_PROTECTED,
    CONF_SCALE,
    CONF_SCAN_SCOPE,
    CONF_SOURCE,
    CONF_SOURCES,
    CONF_STAT_JUMP_KWH,
    CONF_STATISTICS,
    CONF_TARGET_UNIT,
    CONF_TOTAL,
    CONF_UTILITY_METER,
    CONF_UTILITY_METERS,
    MODE_PHASE_SPLIT,
    MODE_SUM,
    SCAN_SCOPE_ALL,
)
from custom_components.energy_guard.hub import get_hub

from .conftest import SOURCE, protected_definition, set_source, setup_guard

PROTECTED = "sensor.grid_import_protected"
PHASE_A = "sensor.phase_a"
PHASE_B = "sensor.phase_b"
TOTAL = "sensor.grid_import_protected"
METER = "sensor.grid_import_monthly"


@pytest.fixture(autouse=True)
def recorder(recorder_mock):
    """Run every test in this module against an in-memory recorder."""
    return recorder_mock


async def _open_section(hass: HomeAssistant, entry, section: str):
    """Open the options menu and pick one section."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": section}
    )


async def _pick(hass: HomeAssistant, entry, section: str, action: str) -> dict:
    """Open a section and choose one of its actions (add/edit/toggle/delete)."""
    result = await _open_section(hass, entry, section)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": f"{section}_{action}"}
    )


async def test_protected_sensor_add_edit_toggle_delete(hass: HomeAssistant) -> None:
    """A protected sensor survives the full add/edit/toggle/delete round trip."""
    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(hass)

    # The section menu of an empty list only offers "add".
    result = await _open_section(hass, entry, CONF_PROTECTED)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["protected_sensors_add"]

    # --- add ------------------------------------------------------------
    result = await _pick(hass, entry, CONF_PROTECTED, "add")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "protected_sensors_add"

    # A name that would collide with the source entity is refused: Home
    # Assistant would otherwise name the new entity "sensor.grid_import_2".
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_NAME: "Grid import", CONF_SOURCE: SOURCE},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "name_in_use"}
    assert entry.options[CONF_PROTECTED] == []

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Grid import protected",
            CONF_SOURCE: SOURCE,
            CONF_TARGET_UNIT: "kWh",
            CONF_OFFSET: 1.5,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    stored = entry.options[CONF_PROTECTED]
    assert len(stored) == 1
    item_id = stored[0][CONF_ID]
    assert item_id
    assert stored[0][CONF_NAME] == "Grid import protected"
    assert stored[0][CONF_SOURCE] == SOURCE
    assert stored[0][CONF_OFFSET] == 1.5
    assert stored[0][CONF_ENABLED] is True
    assert stored[0][CONF_SCALE] == 1.0  # default filled in by the flow
    assert hass.states.get(PROTECTED) is not None

    # --- edit: the entity id must not move ------------------------------
    result = await _pick(hass, entry, CONF_PROTECTED, "edit")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "protected_sensors_edit"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ID: item_id}
    )
    assert result["step_id"] == "protected_sensors_edit_form"
    assert result["description_placeholders"]["id"] == item_id

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Grid import protected",
            CONF_SOURCE: SOURCE,
            CONF_TARGET_UNIT: "kWh",
            CONF_OFFSET: 2.5,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    stored = entry.options[CONF_PROTECTED]
    assert len(stored) == 1
    assert stored[0][CONF_ID] == item_id
    assert stored[0][CONF_OFFSET] == 2.5
    assert hass.states.get(PROTECTED) is not None

    # --- toggle off: the guard stops, the configuration stays -----------
    result = await _pick(hass, entry, CONF_PROTECTED, "toggle")
    assert result["step_id"] == "protected_sensors_toggle"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ID: item_id}
    )
    assert result["step_id"] == "protected_sensors_toggle_confirm"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert entry.options[CONF_PROTECTED][0][CONF_ENABLED] is False
    assert entry.options[CONF_PROTECTED][0][CONF_ID] == item_id
    # A disabled definition keeps its configuration but stops guarding: the
    # entity is unloaded and Home Assistant leaves it as a restored
    # "unavailable" state - never a stale (or false zero) value.
    disabled_state = hass.states.get(PROTECTED)
    assert disabled_state is not None
    assert disabled_state.state == "unavailable"

    # --- toggle back on -------------------------------------------------
    result = await _pick(hass, entry, CONF_PROTECTED, "toggle")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ID: item_id}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_PROTECTED][0][CONF_ENABLED] is True
    enabled_state = hass.states.get(PROTECTED)
    assert enabled_state is not None
    assert enabled_state.state not in ("unavailable", "unknown")
    # 38,243.46 kWh plus the configured offset of 2.5 kWh.
    assert float(enabled_state.state) == pytest.approx(38245.96)

    # --- delete ---------------------------------------------------------
    result = await _pick(hass, entry, CONF_PROTECTED, "delete")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ID: item_id}
    )
    assert result["step_id"] == "protected_sensors_delete_confirm"

    # No confirmation -> the form comes back with an error, nothing deleted.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": False}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"confirm": "confirmation_required"}
    assert len(entry.options[CONF_PROTECTED]) == 1

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_PROTECTED] == []
    deleted_state = hass.states.get(PROTECTED)
    assert deleted_state is None or deleted_state.state == "unavailable"


async def test_protected_sensor_add_requires_a_name(hass: HomeAssistant) -> None:
    """A protected sensor without a name is refused by the form."""
    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(hass)

    result = await _pick(hass, entry, CONF_PROTECTED, "add")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NAME: "   ", CONF_SOURCE: SOURCE}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "name_required"}
    assert entry.options[CONF_PROTECTED] == []


async def test_derived_sensor_sum_and_phase_split(hass: HomeAssistant) -> None:
    """Derived sum and phase-split sensors can be added, disabled and deleted."""
    set_source(hass, SOURCE, 38243.46)
    set_source(hass, PHASE_A, 200.0)
    set_source(hass, PHASE_B, 300.0)
    entry = await setup_guard(hass, protected=[protected_definition()])

    # --- a derived sensor may not take an entity id that is in use -------
    result = await _pick(hass, entry, CONF_DERIVED, "add")
    assert result["step_id"] == "derived_sensors_add"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Grid import protected",  # the protected sensor already
            CONF_MODE: MODE_SUM,
            CONF_SOURCES: [PHASE_A, PHASE_B],
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "name_in_use"}
    assert entry.options[CONF_DERIVED] == []

    # --- add a sum ------------------------------------------------------
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Phase total",
            CONF_MODE: MODE_SUM,
            CONF_SOURCES: [PHASE_A, PHASE_B],
            CONF_TARGET_UNIT: "kWh",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    sums = entry.options[CONF_DERIVED]
    assert len(sums) == 1
    assert sums[0][CONF_MODE] == MODE_SUM
    assert sums[0][CONF_SOURCES] == [PHASE_A, PHASE_B]
    assert sums[0][CONF_ENABLED] is True

    # --- add a phase split ---------------------------------------------
    result = await _pick(hass, entry, CONF_DERIVED, "add")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Phase split",
            CONF_MODE: MODE_PHASE_SPLIT,
            CONF_TOTAL: TOTAL,
            CONF_PARTS: [PHASE_A, PHASE_B],
            CONF_TARGET_UNIT: "kWh",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    derived = entry.options[CONF_DERIVED]
    assert len(derived) == 2
    split = next(item for item in derived if item[CONF_MODE] == MODE_PHASE_SPLIT)
    assert split[CONF_TOTAL] == TOTAL
    assert split[CONF_PARTS] == [PHASE_A, PHASE_B]

    # --- toggle the split off ------------------------------------------
    result = await _pick(hass, entry, CONF_DERIVED, "toggle")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ID: split[CONF_ID]}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    disabled = next(
        item for item in entry.options[CONF_DERIVED] if item[CONF_ID] == split[CONF_ID]
    )
    assert disabled[CONF_ENABLED] is False

    # --- delete it again ------------------------------------------------
    result = await _pick(hass, entry, CONF_DERIVED, "delete")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ID: split[CONF_ID]}
    )
    assert result["step_id"] == "derived_sensors_delete_confirm"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    remaining = entry.options[CONF_DERIVED]
    assert len(remaining) == 1
    assert remaining[0][CONF_MODE] == MODE_SUM


async def test_utility_meter_section_round_trip(hass: HomeAssistant) -> None:
    """A utility meter can be registered, edited and removed."""
    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(hass, protected=[protected_definition()])

    result = await _pick(hass, entry, CONF_UTILITY_METERS, "add")
    assert result["step_id"] == "utility_meters_add"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Grid import monthly",
            CONF_UTILITY_METER: METER,
            CONF_SOURCE: PROTECTED,
            CONF_BASELINE: 38000.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    meters = entry.options[CONF_UTILITY_METERS]
    assert len(meters) == 1
    meter_id = meters[0][CONF_ID]
    assert meters[0][CONF_UTILITY_METER] == METER
    assert meters[0][CONF_SOURCE] == PROTECTED
    assert meters[0][CONF_BASELINE] == 38000.0

    # Edit the baseline.
    result = await _pick(hass, entry, CONF_UTILITY_METERS, "edit")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ID: meter_id}
    )
    assert result["step_id"] == "utility_meters_edit_form"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Grid import monthly",
            CONF_UTILITY_METER: METER,
            CONF_SOURCE: PROTECTED,
            CONF_BASELINE: 38100.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_UTILITY_METERS][0][CONF_ID] == meter_id
    assert entry.options[CONF_UTILITY_METERS][0][CONF_BASELINE] == 38100.0

    # Delete it without confirming: nothing happens.
    result = await _pick(hass, entry, CONF_UTILITY_METERS, "delete")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ID: meter_id}
    )
    assert result["step_id"] == "utility_meters_delete_confirm"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": False}
    )
    assert len(entry.options[CONF_UTILITY_METERS]) == 1

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_UTILITY_METERS] == []


async def test_detection_statistics_cost_and_backup_sections(
    hass: HomeAssistant,
) -> None:
    """The single-form sections store exactly what the user entered."""
    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(hass, protected=[protected_definition()])

    # Detection rules.
    result = await _open_section(hass, entry, CONF_DETECTION)
    assert result["step_id"] == "detection_rules"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 90, "lookback_hours": 72}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_DETECTION]["scan_interval"] == 90
    assert entry.options[CONF_DETECTION]["lookback_hours"] == 72
    # Untouched fields keep their defaults (the hand-built rules survive).
    hub = get_hub(hass, entry.entry_id)
    assert hub is not None
    assert hub.config.detection.scan_interval == 90
    assert hub.config.detection.lookback_hours == 72
    assert hub.config.detection.event_retention_days > 0

    # Statistics repair thresholds (same storage section, different form).
    result = await _open_section(hass, entry, CONF_STATISTICS)
    assert result["step_id"] == "statistics_repair"
    assert "last_scan" in result["description_placeholders"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_STAT_JUMP_KWH: 500.0, CONF_SCAN_SCOPE: SCAN_SCOPE_ALL}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_DETECTION][CONF_STAT_JUMP_KWH] == 500.0
    assert entry.options[CONF_DETECTION][CONF_SCAN_SCOPE] == SCAN_SCOPE_ALL
    # The stored scope is what the scanner uses as its default.
    hub = get_hub(hass, entry.entry_id)
    assert hub is not None
    assert hub.config.detection.scan_scope == SCAN_SCOPE_ALL

    # Cost repair: the GEL tariff used by the documented worked example.
    result = await _open_section(hass, entry, CONF_COST)
    assert result["step_id"] == "cost_repair"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ENABLED: True,
            CONF_PRICE: 0.235,
            CONF_CURRENCY: "GEL",
            CONF_ENERGY_STATISTIC_ID: "sensor.grid_import",
            CONF_COST_STATISTIC_ID: "sensor.grid_import_cost",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    cost = entry.options[CONF_COST]
    assert cost[CONF_ENABLED] is True
    assert cost[CONF_PRICE] == 0.235
    assert cost[CONF_CURRENCY] == "GEL"
    assert cost[CONF_COST_STATISTIC_ID] == "sensor.grid_import_cost"

    # Backups and reports.
    result = await _open_section(hass, entry, CONF_BACKUPS)
    assert result["step_id"] == "backups_reports"
    assert "backup_dir" in result["description_placeholders"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"create_backup": False, "keep_backups": 5}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_BACKUPS] == {
        "create_backup": False,
        "keep_backups": 5,
    }


async def test_review_step_reports_and_changes_nothing(hass: HomeAssistant) -> None:
    """The review step is a read-only summary of what was found."""
    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(hass, protected=[protected_definition()])
    hub = get_hub(hass, entry.entry_id)
    assert hub is not None
    candidates_before = list(hub.scan_candidates)
    repairs_before = list(hub.repairs)

    result = await _open_section(hass, entry, "review")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "review"
    assert result["data_schema"].schema == {}
    assert result["last_step"] is True
    placeholders = result["description_placeholders"]
    for key in (
        "issues",
        "anomalies",
        "estimated_false_energy",
        "candidate_list",
        "repair_hint",
    ):
        assert key in placeholders
    # The hint must never promise an automatic fix.
    assert "Nothing is repaired automatically" in placeholders["repair_hint"]
    assert "energy_guard.repair_statistics" in placeholders["repair_hint"]

    await hass.async_block_till_done()
    assert list(hub.scan_candidates) == candidates_before
    assert list(hub.repairs) == repairs_before
    assert entry.options[CONF_PROTECTED] == [protected_definition()]


def _populated_forms() -> list[tuple[str, object, dict]]:
    """Return (name, schema builder, data) for every options-flow form."""
    from custom_components.energy_guard import selectors as sel

    return [
        (
            "protected_sensors",
            sel.protected_schema,
            {
                CONF_NAME: "Grid import protected",
                CONF_SOURCE: SOURCE,
                CONF_OFFSET: 1.5,
                "precision": 3,
                "max_value": "999999",
            },
        ),
        (
            "derived_sensors",
            sel.derived_schema,
            {
                CONF_NAME: "Phase split",
                CONF_MODE: MODE_PHASE_SPLIT,
                CONF_SOURCES: [],
                CONF_TOTAL: TOTAL,
                CONF_PARTS: [PHASE_A, PHASE_B],
            },
        ),
        (
            "utility_meters",
            sel.utility_meter_schema,
            {
                CONF_NAME: "Grid import monthly",
                CONF_UTILITY_METER: METER,
                CONF_SOURCE: PROTECTED,
                CONF_BASELINE: 38000.0,
            },
        ),
        (
            "detection_rules",
            sel.detection_schema,
            {"scan_interval": 120, "lookback_hours": 48},
        ),
        (
            "statistics_repair",
            sel.statistics_schema,
            {CONF_STAT_JUMP_KWH: 500.0},
        ),
        (
            "cost_repair",
            sel.cost_schema,
            {CONF_PRICE: 0.235, CONF_CURRENCY: "GEL", CONF_ENABLED: True},
        ),
        ("backups_reports", sel.backups_schema, {"create_backup": True}),
    ]


def test_every_options_form_renders_and_validates() -> None:
    """Regression: every form of the options flow can be built and submitted.

    Two Home Assistant selector details used to make five of the seven sections
    unusable (so no protected sensor, derived sensor or tariff could be added
    from the UI at all):

    * ``NumberSelectorConfig(max=None)`` fails validation with
      ``expected float at 'max'`` - unset optional values must be omitted.
    * ``EntitySelector`` rejects ``None``, so an *optional* entity field whose
      default is ``None`` (an omitted total, an omitted meter source) made the
      whole form invalid: ``Entity None is neither a valid entity ID``.

    The forms are built and validated here without a running Home Assistant.
    """
    from custom_components.energy_guard import selectors as sel

    # 1. Every form renders - with no data and with filled-in data.
    for name, builder in (
        ("protected", sel.protected_schema),
        ("derived", sel.derived_schema),
        ("utility_meter", sel.utility_meter_schema),
        ("detection", sel.detection_schema),
        ("statistics", sel.statistics_schema),
        ("cost", sel.cost_schema),
        ("backups", sel.backups_schema),
    ):
        assert builder().schema, name
        assert builder(builder().schema and {}) is not None, name

    for name, builder, data in _populated_forms():
        assert builder(data).schema, name

    # 2. Optional entity fields may be left empty.
    derived = sel.derived_schema()(
        {
            CONF_NAME: "Phase total",
            CONF_MODE: MODE_SUM,
            CONF_SOURCES: [PHASE_A, PHASE_B],
            CONF_TOTAL: None,
            CONF_PARTS: [],
        }
    )
    assert derived[CONF_TOTAL] is None
    assert derived[CONF_PARTS] == []

    meter = sel.utility_meter_schema()(
        {
            CONF_NAME: "Meter",
            CONF_UTILITY_METER: METER,
            CONF_SOURCE: None,
        }
    )
    assert meter[CONF_SOURCE] is None

    # 3. Required entity fields are still validated.
    with pytest.raises(vol.Invalid):
        sel.protected_schema()({CONF_NAME: "x", CONF_SOURCE: "not an entity"})


async def test_every_options_section_opens_in_a_real_flow(
    hass: HomeAssistant,
) -> None:
    """Each menu entry of a running entry opens (and can be left) without error."""
    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(hass, protected=[protected_definition()])

    list_sections = (CONF_PROTECTED, CONF_DERIVED, CONF_UTILITY_METERS)
    for section in (
        *list_sections,
        CONF_DETECTION,
        CONF_STATISTICS,
        CONF_COST,
        CONF_BACKUPS,
        "review",
    ):
        result = await _open_section(hass, entry, section)
        expected = (
            FlowResultType.MENU if section in list_sections else FlowResultType.FORM
        )
        assert result["type"] is expected, section
        assert result["step_id"] == section, section
