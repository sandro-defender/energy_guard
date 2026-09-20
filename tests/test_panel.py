"""Configuration panel tests: registration, safety and the served asset.

The panel itself is a browser web component (it cannot run in this test suite),
but everything that makes it safe and reachable can be tested:

* it is only registered when a frontend exists - a headless installation keeps
  working and nothing depends on it,
* a registration failure is logged and ignored (protection never depends on UI),
* when it is registered, it is an admin-only sidebar panel and goes away on
  unload,
* the served JavaScript is present, defines the element the panel registers,
  only talks to commands that really exist, and never sends a confirmation
  without asking the user first.
"""

from __future__ import annotations

import logging
import re

import pytest
from homeassistant.core import HomeAssistant

from custom_components.energy_guard import panel as panel_module
from custom_components.energy_guard import websocket as ws_module
from custom_components.energy_guard.const import DOMAIN
from custom_components.energy_guard.panel import (
    PANEL_ELEMENT,
    PANEL_JS_URL,
    PANEL_URL_PATH,
    panel_is_registered,
)

from .conftest import SOURCE, protected_definition, set_source, setup_guard

PANEL_JS = panel_module._FRONTEND_DIR / "energy_guard-panel.js"


def _code() -> str:
    """Return the panel JavaScript with comments and strings stripped.

    The header comment documents the safety rules (it mentions confirmations and
    passwords on purpose), so only real code may be checked.
    """
    source = PANEL_JS.read_text()
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    source = re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)
    return source


def _methods(source: str) -> dict[str, str]:
    """Return ``{method name: body}`` for the panel class."""
    bodies: dict[str, str] = {}
    current: str | None = None
    for line in source.splitlines():
        match = re.match(r"\s{2}(?:async\s+)?([a-zA-Z_][\w]*)\s*\(", line)
        if match:
            current = match.group(1)
            bodies[current] = ""
        elif current is not None:
            bodies[current] += line + "\n"
    return bodies


@pytest.fixture(autouse=True)
def recorder(recorder_mock):
    """Run every test in this module against an in-memory recorder."""
    return recorder_mock


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------
async def test_panel_is_skipped_without_a_frontend(hass: HomeAssistant) -> None:
    """A headless Home Assistant keeps working without the panel."""
    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(hass, protected=[protected_definition()])

    assert panel_is_registered(hass) is False
    assert "frontend_panels" not in hass.data
    # The integration is fully functional: the protected sensor is live.
    assert hass.states.get("sensor.grid_import_protected") is not None
    # Its API is registered anyway, so a panel can connect after a restart.
    assert hass.data[DOMAIN][f"{DOMAIN}_websocket_registered"] is True
    assert entry.state.name == "LOADED"


async def test_panel_is_registered_when_a_frontend_exists(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a frontend the sidebar entry is created, admin-only."""
    pytest.importorskip("homeassistant.components.panel_custom")
    frontend = pytest.importorskip("homeassistant.components.frontend")
    hass.data.setdefault(frontend.DATA_PANELS, {})
    monkeypatch.setattr(panel_module, "_frontend_ready", lambda _hass: True)
    monkeypatch.setattr(
        panel_module,
        "_FRONTEND_DIR",
        panel_module._FRONTEND_DIR,
    )
    # hass.http is only present with the http component; the panel only needs it
    # for the static asset, so register a minimal stand-in.
    monkeypatch.setattr(hass, "http", _FakeHttp(), raising=False)

    set_source(hass, SOURCE, 38243.46)
    await setup_guard(hass, protected=[protected_definition()])

    assert panel_is_registered(hass) is True
    panels = hass.data[frontend.DATA_PANELS]
    assert PANEL_URL_PATH in panels
    registered = panels[PANEL_URL_PATH]
    # Home Assistant 2026.9 stores a Panel object; older versions stored a dict.
    data = registered.__dict__ if hasattr(registered, "__dict__") else dict(registered)
    assert data["component_name"] == "custom"
    custom = data["config"]["_panel_custom"]
    assert custom["name"] == PANEL_ELEMENT
    assert custom["module_url"] == PANEL_JS_URL
    assert data.get("require_admin") is True


async def test_panel_is_removed_on_unload(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unloading the last entry removes the sidebar entry."""
    frontend = pytest.importorskip("homeassistant.components.frontend")
    pytest.importorskip("homeassistant.components.panel_custom")
    hass.data.setdefault(frontend.DATA_PANELS, {})
    monkeypatch.setattr(panel_module, "_frontend_ready", lambda _hass: True)
    monkeypatch.setattr(hass, "http", _FakeHttp(), raising=False)

    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(hass, protected=[protected_definition()])
    assert PANEL_URL_PATH in hass.data[frontend.DATA_PANELS]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert PANEL_URL_PATH not in hass.data[frontend.DATA_PANELS]
    assert panel_is_registered(hass) is False


async def test_a_failing_panel_registration_never_breaks_setup(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unexpected frontend error is logged, never fatal."""
    monkeypatch.setattr(panel_module, "_frontend_ready", lambda _hass: True)
    monkeypatch.setattr(hass, "http", _FakeHttp(), raising=False)

    async def _boom(*args, **kwargs):
        raise RuntimeError("frontend changed")

    monkeypatch.setattr(
        "homeassistant.components.panel_custom.async_register_panel", _boom
    )

    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(hass, protected=[protected_definition()])

    assert entry.state.name == "LOADED"
    assert panel_is_registered(hass) is False
    assert hass.states.get("sensor.grid_import_protected") is not None
    warnings = [
        record for record in caplog.records if record.levelno >= logging.WARNING
    ]
    assert any("configuration panel" in record.getMessage() for record in warnings)


class _FakeHttp:
    """Minimal stand-in for ``hass.http`` (the test suite has no HTTP server)."""

    def __init__(self) -> None:
        self.static_paths: list[object] = []

    async def async_register_static_paths(self, configs) -> None:
        """Record the static paths the panel registers."""
        self.static_paths.extend(configs)


# ---------------------------------------------------------------------------
# the served asset
# ---------------------------------------------------------------------------
def test_panel_asset_is_present_and_registers_the_element() -> None:
    """The JavaScript file exists and defines the element we register."""
    assert PANEL_JS.exists()
    source = PANEL_JS.read_text()
    assert source.strip()
    assert f'customElements.define("{PANEL_ELEMENT}"' in source
    assert "attachShadow" in source, "the panel must not leak styles into HA"


def test_panel_only_uses_commands_that_exist() -> None:
    """Every WebSocket command the panel sends is registered in Python."""
    source = _code()
    commands = {
        line.split('"')[1]
        for line in source.splitlines()
        if "energy_guard/config/" in line and '"' in line
    }
    known = {
        ws_module.WS_GET,
        ws_module.WS_SET,
        ws_module.WS_DEFINITION,
        ws_module.WS_CHOICES,
        ws_module.WS_REVIEW,
        ws_module.WS_FILES,
        ws_module.WS_TEMPLATES,
        ws_module.WS_SUBSCRIBE,
    }
    assert commands, "the panel should send at least one command"
    assert commands <= known, commands - known


def test_panel_confirms_before_every_data_change() -> None:
    """No confirmation flag is sent from a method that does not ask the user."""
    methods = _methods(_code())
    assert methods, "the panel class should have methods"
    # Deleting a definition asks for confirmation in the UI ...
    assert "window.confirm(" in methods["_attach"]
    # ... and every method that *sends* a confirmation flag asks too.  Only the
    # part of the body after the service call counts: the notes in the rendered
    # HTML mention confirmations as text.
    flagged = []
    for name, body in methods.items():
        if "callService(" not in body:
            continue
        payload = body[body.index("callService(") :]
        if "confirm: true" in payload or "confirm_cost: true" in payload:
            flagged.append(name)
    assert flagged, "the repair flow should exist"
    for name in flagged:
        assert "window.confirm(" in methods[name], name
    # Deletion is only sent from the delete handler, which confirmed first.
    delete_handler = methods["_attach"]
    assert "confirm: true" in delete_handler
    assert "window.confirm(" in delete_handler


def test_panel_never_handles_credentials() -> None:
    """The panel code contains no token, password or database handling."""
    source = _code().lower()
    for forbidden in (
        "password",
        "bearer ",
        "access_token",
        "authorization",
        ".storage/",
        "home-assistant_v2.db",
        "sqlite",
    ):
        assert forbidden not in source, forbidden


def test_panel_only_calls_the_expected_services() -> None:
    """Only the documented services are called, and repairs are preview-first."""
    source = _code()
    called = {
        part.split('"')[1] for part in source.split("callService(")[1:] if '"' in part
    }
    assert called <= {
        "energy_guard",
        "scan_statistics",
        "repair_statistics",
        "export_repair_report",
        "export_templates",
    }, called
    methods = _methods(source)
    apply_repair = methods["_applyRepair"]
    assert "dry_run" not in apply_repair, "the apply step is not a dry run"
    assert "confirm: true" in apply_repair
    # The preview steps never confirm.
    assert "dry_run: true" in methods["_previewRepair"]
    assert "confirm" not in methods["_previewRepair"]
    assert "dry_run: true" in methods["_previewCost"]
    assert "confirm_cost" in methods["_applyCost"]


def test_panel_form_fields_match_the_real_schemas() -> None:
    """Every form field of the panel is a field the backend schema accepts.

    This is the anti-drift check that keeps the panel and ``selectors.py`` in
    step: a renamed option (for example ``entity_id`` ->
    ``utility_meter_entity_id``) fails here instead of failing in the browser.
    """
    from custom_components.energy_guard.selectors import (
        backups_schema,
        cost_schema,
        derived_schema,
        detection_schema,
        protected_schema,
        statistics_schema,
        utility_meter_schema,
    )

    schemas = {
        "protected_sensors": protected_schema,
        "derived_sensors": derived_schema,
        "utility_meters": utility_meter_schema,
        "detection_rules": detection_schema,
        "statistics_repair": statistics_schema,
        "cost_repair": cost_schema,
        "backups_reports": backups_schema,
    }
    source = _code()
    block = source[source.index("const FIELDS = {") : source.index("const CARD = `")]
    current = None
    found: dict[str, list[str]] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.endswith(": [") or stripped.endswith(": ["):
            current = stripped.split(":")[0]
            found[current] = []
        elif current and stripped.startswith("{ key:"):
            key = stripped.split('"')[1]
            found[current].append(key)

    assert set(found) == set(schemas), set(found) ^ set(schemas)
    for section, keys in found.items():
        allowed = {str(key) for key in schemas[section]().schema}
        unknown = [key for key in keys if key not in allowed]
        assert not unknown, f"{section}: {unknown}"


def test_the_scan_buttons_are_read_only() -> None:
    """The only single-click service call is the read-only scan."""
    methods = _methods(_code())
    scan = methods["_scan"]
    assert '"scan_statistics"' in scan
    assert "confirm" not in scan
    assert "dry_run" not in scan
