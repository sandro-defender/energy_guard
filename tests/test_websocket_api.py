"""Configuration WebSocket API tests (the backend of the sidebar panel).

The panel is a thin client: everything it can do goes through these commands,
so this file is where "the panel cannot store something the options flow would
refuse" is actually proven.  Covered here:

* every command is admin only,
* reading returns the stored configuration,
* writing a settings section validates, merges and reloads,
* definition add/update/toggle/delete keeps the identity (and therefore the
  entity id) of an existing definition,
* names that would collide are refused,
* delete needs an explicit confirmation,
* the read-only commands (files, templates) expose no paths,
* a failing write changes nothing.
"""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.energy_guard.const import (
    CONF_DERIVED,
    CONF_DETECTION,
    CONF_PROTECTED,
    CONF_SCAN_SCOPE,
    SCAN_SCOPE_ALL,
    SECTION_BACKUPS,
    SECTION_COST,
    SECTION_DERIVED,
    SECTION_METERS,
    SECTION_PROTECTED,
    SECTION_STATISTICS,
)
from custom_components.energy_guard.hub import get_hub
from custom_components.energy_guard.websocket import (
    WS_CHOICES,
    WS_DASHBOARD,
    WS_DEFINITION,
    WS_FILES,
    WS_GET,
    WS_REVIEW,
    WS_SET,
    WS_SUBSCRIBE,
    WS_TEMPLATES,
)

from .conftest import SOURCE, protected_definition, set_source, setup_guard

pytestmark = pytest.mark.usefixtures("recorder")


@pytest.fixture(autouse=True)
def recorder(recorder_mock):
    """Run every test in this module against an in-memory recorder."""
    return recorder_mock


async def _connect(hass, hass_ws_client, access_token: str | None = None):
    """Connect and return the client (admin unless a token says otherwise)."""
    if access_token is None:
        return await hass_ws_client(hass)
    return await hass_ws_client(hass, access_token=access_token)


async def _call(client, msg: dict) -> dict:
    """Send a command and return the response."""
    await client.send_json(msg)
    return await client.receive_json()


async def _setup(hass: HomeAssistant):
    """Set up Energy Guard with one protected sensor."""
    set_source(hass, SOURCE, 38243.46)
    return await setup_guard(hass, protected=[protected_definition()])


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------
async def test_get_returns_the_configuration(
    hass: HomeAssistant, hass_ws_client
) -> None:
    """`config/get` returns the stored options and the panel metadata."""
    entry = await _setup(hass)
    client = await _connect(hass, hass_ws_client)

    response = await _call(client, {"id": 1, "type": WS_GET})

    assert response["success"] is True
    result = response["result"]
    assert result["entry_id"] == entry.entry_id
    assert result["integration"] == "Energy Guard"
    assert len(result["config"][CONF_PROTECTED]) == 1
    assert result["config"][CONF_PROTECTED][0]["name"] == "Grid import protected"
    assert SECTION_STATISTICS in result["settings_sections"]
    assert SECTION_PROTECTED in result["definition_sections"]
    assert result["last_scan"]["scope"] is None
    assert "timestamp" in result["last_scan"]


async def test_choices_and_review_and_files_and_templates(
    hass: HomeAssistant, hass_ws_client
) -> None:
    """The other read commands answer with usable, path-free data."""
    await _setup(hass)
    client = await _connect(hass, hass_ws_client)

    choices = await _call(client, {"id": 2, "type": WS_CHOICES})
    assert choices["success"] is True
    entity_ids = {item["entity_id"] for item in choices["result"]["entities"]}
    assert SOURCE in entity_ids
    assert "kWh" in choices["result"]["units"]
    assert "sum" in choices["result"]["modes"]
    assert choices["result"]["scan_scopes"] == ["linked", "energy", "all"]

    review = await _call(client, {"id": 3, "type": WS_REVIEW})
    assert review["success"] is True
    assert review["result"]["candidates"] == []

    files = await _call(client, {"id": 4, "type": WS_FILES})
    assert files["success"] is True
    assert files["result"]["backups"] == []
    serialised = str(files["result"])
    assert "/" not in serialised.split("note")[0], "file paths must not be exposed"

    templates = await _call(client, {"id": 5, "type": WS_TEMPLATES})
    assert templates["success"] is True
    assert "energy_guard" in templates["result"]["yaml"]
    assert "Grid import protected" in templates["result"]["yaml"]


async def test_dashboard_returns_the_dashboard_data(
    hass: HomeAssistant, hass_ws_client
) -> None:
    """`config/dashboard` answers with the aggregated, read-only dashboard."""
    entry = await _setup(hass)
    client = await _connect(hass, hass_ws_client)

    response = await _call(client, {"id": 6, "type": WS_DASHBOARD})
    assert response["success"] is True
    result = response["result"]
    assert result["entry_id"] == entry.entry_id
    dashboard = result["dashboard"]
    for key in (
        "generated_at",
        "protection",
        "points",
        "by_kind",
        "recent",
        "scan",
        "counts",
        "actions",
    ):
        assert key in dashboard, key
    assert dashboard["counts"]["protected"] == 1
    assert dashboard["counts"]["protected_enabled"] == 1
    # Nothing about the request changed the hub: the command is read-only.
    hub = get_hub(hass, entry.entry_id)
    assert hub is not None
    assert dashboard["protection"]["events_total"] == len(hub.events)


# ---------------------------------------------------------------------------
# admin only
# ---------------------------------------------------------------------------
async def test_every_command_is_admin_only(
    hass: HomeAssistant, hass_ws_client, hass_read_only_access_token: str
) -> None:
    """A read-only user cannot read or write the configuration."""
    await _setup(hass)
    client = await _connect(hass, hass_ws_client, hass_read_only_access_token)

    for msg_id, command in enumerate(
        (WS_GET, WS_CHOICES, WS_REVIEW, WS_DASHBOARD, WS_FILES, WS_TEMPLATES),
        start=10,
    ):
        response = await _call(client, {"id": msg_id, "type": command})
        assert response["success"] is False, command
        assert response["error"]["code"] == "unauthorized", command

    response = await _call(
        client,
        {
            "id": 20,
            "type": WS_SET,
            "section": SECTION_STATISTICS,
            "data": {CONF_SCAN_SCOPE: SCAN_SCOPE_ALL},
        },
    )
    assert response["success"] is False
    assert response["error"]["code"] == "unauthorized"


# ---------------------------------------------------------------------------
# writing settings
# ---------------------------------------------------------------------------
async def test_set_validates_merges_and_reloads(
    hass: HomeAssistant, hass_ws_client
) -> None:
    """A settings section is validated, merged with the stored values, reloaded."""
    entry = await _setup(hass)
    client = await _connect(hass, hass_ws_client)

    before = get_hub(hass, entry.entry_id)
    assert before.config.detection.scan_scope == "linked"

    response = await _call(
        client,
        {
            "id": 30,
            "type": WS_SET,
            "section": SECTION_STATISTICS,
            "data": {
                CONF_SCAN_SCOPE: SCAN_SCOPE_ALL,
                "statistics_jump_threshold": 250.0,
            },
        },
    )

    assert response["success"] is True
    assert response["result"]["stored_under"] == CONF_DETECTION
    assert entry.options[CONF_DETECTION][CONF_SCAN_SCOPE] == SCAN_SCOPE_ALL
    assert entry.options[CONF_DETECTION]["statistics_jump_threshold"] == 250.0
    # The other detection rules survive the merge.
    hub_after = get_hub(hass, entry.entry_id)
    assert hub_after.config.detection.scan_interval > 0
    assert hub_after.config.detection.issue_window_hours > 0

    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    hub = get_hub(hass, entry.entry_id)
    assert hub.config.detection.scan_scope == SCAN_SCOPE_ALL
    assert hub.config.detection.statistics_jump_threshold == 250.0


async def test_set_rejects_invalid_values(hass: HomeAssistant, hass_ws_client) -> None:
    """An out-of-range value is refused and nothing is written."""
    entry = await _setup(hass)
    client = await _connect(hass, hass_ws_client)
    options_before = dict(entry.options)

    response = await _call(
        client,
        {
            "id": 31,
            "type": WS_SET,
            "section": SECTION_STATISTICS,
            "data": {CONF_SCAN_SCOPE: "everything"},
        },
    )

    assert response["success"] is False
    assert "scan_scope" in response["error"]["message"]
    assert entry.options == options_before


async def test_set_refuses_a_definition_section(
    hass: HomeAssistant, hass_ws_client
) -> None:
    """Definitions are not written through the settings command."""
    await _setup(hass)
    client = await _connect(hass, hass_ws_client)

    response = await _call(
        client,
        {
            "id": 32,
            "type": WS_SET,
            "section": SECTION_PROTECTED,
            "data": {"name": "Nope"},
        },
    )

    assert response["success"] is False
    assert "definitions" in response["error"]["message"]


async def test_cost_and_backup_sections(hass: HomeAssistant, hass_ws_client) -> None:
    """The tariff and the backup settings are writable from the panel."""
    entry = await _setup(hass)
    client = await _connect(hass, hass_ws_client)

    cost = await _call(
        client,
        {
            "id": 33,
            "type": WS_SET,
            "section": SECTION_COST,
            "data": {"enabled": True, "price": 0.235, "currency": "GEL"},
        },
    )
    assert cost["success"] is True
    assert entry.options["cost_repair"]["price"] == 0.235
    assert entry.options["cost_repair"]["currency"] == "GEL"

    backups = await _call(
        client,
        {
            "id": 34,
            "type": WS_SET,
            "section": SECTION_BACKUPS,
            "data": {"create_backup": True, "keep_backups": 7},
        },
    )
    assert backups["success"] is True
    assert entry.options["backups_reports"]["keep_backups"] == 7


# ---------------------------------------------------------------------------
# definitions
# ---------------------------------------------------------------------------
async def test_add_edit_toggle_delete_a_protected_sensor(
    hass: HomeAssistant, hass_ws_client
) -> None:
    """The panel can manage the whole life cycle of a definition."""
    entry = await _setup(hass)
    client = await _connect(hass, hass_ws_client)

    added = await _call(
        client,
        {
            "id": 40,
            "type": WS_DEFINITION,
            "section": SECTION_PROTECTED,
            "action": "add",
            "data": {"name": "Garage protected", "source_entity_id": "sensor.garage"},
        },
    )
    assert added["success"] is True
    item = added["result"]["item"]
    assert item["name"] == "Garage protected"
    assert item["enabled"] is True  # defaulted
    assert item["unit_of_measurement"] == "kWh"  # defaulted
    assert item["confirm_scans"] == 2  # defaulted
    assert item["id"]

    # An edit keeps the id, and therefore the entity id / unique id.
    updated = await _call(
        client,
        {
            "id": 41,
            "type": WS_DEFINITION,
            "section": SECTION_PROTECTED,
            "action": "update",
            "definition_id": item["id"],
            "data": {
                "name": "Garage protected",
                "source_entity_id": "sensor.garage",
                "confirm_scans": 4,
            },
        },
    )
    assert updated["success"] is True
    assert updated["result"]["item"]["id"] == item["id"]
    assert updated["result"]["item"]["confirm_scans"] == 4

    toggled = await _call(
        client,
        {
            "id": 42,
            "type": WS_DEFINITION,
            "section": SECTION_PROTECTED,
            "action": "toggle",
            "definition_id": item["id"],
        },
    )
    assert toggled["success"] is True
    assert toggled["result"]["item"]["enabled"] is False

    unconfirmed = await _call(
        client,
        {
            "id": 43,
            "type": WS_DEFINITION,
            "section": SECTION_PROTECTED,
            "action": "delete",
            "definition_id": item["id"],
        },
    )
    assert unconfirmed["success"] is False
    assert "confirmation" in unconfirmed["error"]["message"]

    deleted = await _call(
        client,
        {
            "id": 44,
            "type": WS_DEFINITION,
            "section": SECTION_PROTECTED,
            "action": "delete",
            "definition_id": item["id"],
            "confirm": True,
        },
    )
    assert deleted["success"] is True
    assert {entry["id"] for entry in entry.options[CONF_PROTECTED]} == {
        protected_definition()["id"]
    }


async def test_add_refuses_a_colliding_name(
    hass: HomeAssistant, hass_ws_client
) -> None:
    """A name whose entity id is taken is refused with a helpful message."""
    entry = await _setup(hass)
    client = await _connect(hass, hass_ws_client)

    response = await _call(
        client,
        {
            "id": 50,
            "type": WS_DEFINITION,
            "section": SECTION_PROTECTED,
            "action": "add",
            "data": {"name": "Grid import", "source_entity_id": SOURCE},
        },
    )

    assert response["success"] is False
    assert "already taken" in response["error"]["message"]
    assert len(entry.options[CONF_PROTECTED]) == 1, "nothing may be stored"


async def test_add_refuses_a_missing_source(
    hass: HomeAssistant, hass_ws_client
) -> None:
    """A definition without a source (or name) is refused."""
    await _setup(hass)
    client = await _connect(hass, hass_ws_client)

    response = await _call(
        client,
        {
            "id": 51,
            "type": WS_DEFINITION,
            "section": SECTION_PROTECTED,
            "action": "add",
            "data": {"name": "No source"},
        },
    )

    assert response["success"] is False
    assert response["error"]["message"]


async def test_definitions_for_derived_and_meters(
    hass: HomeAssistant, hass_ws_client
) -> None:
    """Derived sensors and utility meters are manageable too."""
    entry = await _setup(hass)
    client = await _connect(hass, hass_ws_client)

    derived = await _call(
        client,
        {
            "id": 60,
            "type": WS_DEFINITION,
            "section": SECTION_DERIVED,
            "action": "add",
            "data": {
                "name": "Phases total",
                "mode": "sum",
                "source_entity_ids": ["sensor.phase_a", "sensor.phase_b"],
            },
        },
    )
    assert derived["success"] is True
    assert derived["result"]["item"]["mode"] == "sum"
    assert entry.options[CONF_DERIVED][0]["name"] == "Phases total"

    meter = await _call(
        client,
        {
            "id": 61,
            "type": WS_DEFINITION,
            "section": SECTION_METERS,
            "action": "add",
            "data": {
                "name": "Grid import monthly",
                "utility_meter_entity_id": "sensor.grid_import_monthly",
                "source_entity_id": "sensor.grid_import_protected",
            },
        },
    )
    assert meter["success"] is True
    assert (
        entry.options["utility_meters"][0]["utility_meter_entity_id"]
        == "sensor.grid_import_monthly"
    )

    # The statistics section still refuses definitions.
    wrong = await _call(
        client,
        {
            "id": 62,
            "type": WS_DEFINITION,
            "section": SECTION_STATISTICS,
            "action": "add",
            "data": {"name": "Nope"},
        },
    )
    assert wrong["success"] is False


async def test_unknown_definition_is_reported(
    hass: HomeAssistant, hass_ws_client
) -> None:
    """Editing something that no longer exists fails cleanly."""
    await _setup(hass)
    client = await _connect(hass, hass_ws_client)

    response = await _call(
        client,
        {
            "id": 70,
            "type": WS_DEFINITION,
            "section": SECTION_PROTECTED,
            "action": "toggle",
            "definition_id": "does-not-exist",
        },
    )

    assert response["success"] is False
    assert "no longer exists" in response["error"]["message"]


# ---------------------------------------------------------------------------
# subscriptions
# ---------------------------------------------------------------------------
async def test_subscribers_receive_the_new_configuration(
    hass: HomeAssistant, hass_ws_client
) -> None:
    """A write is pushed to every subscribed panel."""
    entry = await _setup(hass)
    client = await _connect(hass, hass_ws_client)

    subscribed = await _call(client, {"id": 80, "type": WS_SUBSCRIBE})
    assert subscribed["success"] is True

    await client.send_json(
        {
            "id": 81,
            "type": WS_SET,
            "section": SECTION_STATISTICS,
            "data": {CONF_SCAN_SCOPE: SCAN_SCOPE_ALL},
        }
    )
    for _ in range(5):
        message = await asyncio.wait_for(client.receive_json(), timeout=5)
        if message.get("type") == "event" and message["event"].get("entry_id"):
            break
    else:  # pragma: no cover - defensive
        pytest.fail("no configuration event was pushed")

    assert message["event"]["entry_id"] == entry.entry_id
    assert message["event"]["config"][CONF_DETECTION][CONF_SCAN_SCOPE] == SCAN_SCOPE_ALL
