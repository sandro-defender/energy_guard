"""WebSocket API behind the Energy Guard configuration panel.

The sidebar panel is a small web component that talks to these commands.  Every
command is **admin only** and every write goes through
:mod:`config_api`, the same validation and storage layer the options flow uses,
so the panel can never store something the options flow would refuse.

Overview (all payloads and responses are documented in
``docs/CONFIGURATION.md``):

``energy_guard/config/get``
    the current configuration, the list of sections and the limits of the forms
``energy_guard/config/set``
    merge a validated settings section (thresholds, tariff, backups)
``energy_guard/config/definition``
    add / update / toggle / delete one protected, derived or meter definition
``energy_guard/config/choices``
    the values a form needs: candidate source entities, units, modes, scopes
``energy_guard/config/review``
    the current scan candidates, cost suggestions and recent actions
``energy_guard/config/files``
    the backup and report files Energy Guard wrote (names only, no paths)
``energy_guard/config/templates``
    the generated YAML copy of the configuration
``energy_guard/config/subscribe``
    push the new configuration after every change made through this API

Repairs, calibration, clearing and exporting keep using the normal services -
the panel calls them through ``hass.callService`` exactly like an automation
would, so the confirmation, backup and verification rules stay untouched.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Final

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .config_api import (
    ConfigError,
    async_definition_action,
    async_set_section,
    is_definition_section,
    is_settings_section,
    section_key,
)
from .const import (
    CONF_BACKUPS,
    CONF_COST,
    CONF_DERIVED,
    CONF_DETECTION,
    CONF_PROTECTED,
    CONF_UTILITY_METERS,
    DEFAULT_SCAN_SCOPE,
    DOMAIN,
    NAME,
    SCAN_SCOPES,
    SECTIONS,
    VERSION,
)
from .export import render_templates
from .hub import get_hub
from .selectors import DERIVED_MODES, MODE_OPTIONS
from .units import is_energy_unit

_LOGGER = logging.getLogger(__name__)

WS_PREFIX: Final = "energy_guard/config"
WS_GET: Final = f"{WS_PREFIX}/get"
WS_SET: Final = f"{WS_PREFIX}/set"
WS_DEFINITION: Final = f"{WS_PREFIX}/definition"
WS_CHOICES: Final = f"{WS_PREFIX}/choices"
WS_REVIEW: Final = f"{WS_PREFIX}/review"
WS_FILES: Final = f"{WS_PREFIX}/files"
WS_TEMPLATES: Final = f"{WS_PREFIX}/templates"
WS_SUBSCRIBE: Final = f"{WS_PREFIX}/subscribe"
WS_EVENT: Final = f"{WS_PREFIX}/updated"

#: How many candidate source entities a form offers (kept small on purpose).
MAX_CHOICES: Final = 250

_REGISTERED = f"{DOMAIN}_websocket_registered"
_SUBSCRIBERS = f"{DOMAIN}_websocket_subscribers"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _error(msg_id: int, message: str) -> tuple[int, str, str]:
    """Return the ``(id, code, message)`` tuple of a command error."""
    return (msg_id, "energy_guard_error", message)


def _config(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Return the complete configuration of one entry."""
    options = dict(entry.options)
    return {
        CONF_PROTECTED: [dict(item) for item in options.get(CONF_PROTECTED) or []],
        CONF_DERIVED: [dict(item) for item in options.get(CONF_DERIVED) or []],
        CONF_UTILITY_METERS: [
            dict(item) for item in options.get(CONF_UTILITY_METERS) or []
        ],
        CONF_DETECTION: dict(options.get(CONF_DETECTION) or {}),
        CONF_COST: dict(options.get(CONF_COST) or {}),
        CONF_BACKUPS: dict(options.get(CONF_BACKUPS) or {}),
    }


def _entry_or_error(hass: HomeAssistant, msg: dict[str, Any], entry_id: str | None):
    """Return ``(entry, None)`` or ``(None, error_tuple)``."""
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry_id is None or entry.entry_id == entry_id
    ]
    if not entries:
        return None, _error(
            msg["id"],
            "Energy Guard is not set up. Add the integration first.",
        )
    return entries[0], None


@callback
def _notify_subscribers(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Push the new configuration to every subscribed panel."""
    subscribers: dict[str, list[tuple[int, Any]]] = hass.data.setdefault(
        DOMAIN, {}
    ).setdefault(_SUBSCRIBERS, {})
    for msg_id, connection in list(subscribers.get(entry.entry_id) or []):
        connection.send_event(
            msg_id, {"entry_id": entry.entry_id, "config": _config(hass, entry)}
        )


# ---------------------------------------------------------------------------
# read commands
# ---------------------------------------------------------------------------
@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_GET})
@websocket_api.async_response
async def ws_get(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Return the current configuration and the metadata a panel needs."""
    entry, error = _entry_or_error(hass, msg, msg.get("entry_id"))
    if error is not None:
        connection.send_error(*error)
        return
    hub = get_hub(hass, entry.entry_id)
    connection.send_result(
        msg["id"],
        {
            "config": _config(hass, entry),
            "entry_id": entry.entry_id,
            "entry_title": entry.title,
            "version": VERSION,
            "integration": NAME,
            "sections": list(SECTIONS),
            "definition_sections": [
                section for section in SECTIONS if is_definition_section(section)
            ],
            "settings_sections": [
                section for section in SECTIONS if is_settings_section(section)
            ],
            "limits": {
                "max_choices": MAX_CHOICES,
                # The panel warns before the scanner truncates a wide scan.
                "max_discovered_statistics": 500,
            },
            "last_scan": {
                "timestamp": hub.last_scan.isoformat()
                if hub is not None and hub.last_scan
                else None,
                "scope": hub.last_scan_scope if hub is not None else None,
                "statistic_count": hub.last_scan_statistic_count
                if hub is not None
                else 0,
                "error": hub.last_scan_error if hub is not None else None,
            },
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_CHOICES})
@websocket_api.async_response
async def ws_choices(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Return the values the panel forms offer."""
    _, error = _entry_or_error(hass, msg, msg.get("entry_id"))
    if error is not None:
        connection.send_error(*error)
        return

    entities: list[dict[str, Any]] = []
    entity_ids: list[dict[str, str]] = []
    for state in hass.states.async_all():
        attributes = state.attributes
        unit = attributes.get("unit_of_measurement")
        device_class = attributes.get("device_class")
        state_class = attributes.get("state_class")
        entity_ids.append(
            {
                "entity_id": state.entity_id,
                "name": str(attributes.get("friendly_name") or state.entity_id),
                "unit": str(unit) if unit else "",
                "device_class": str(device_class) if device_class else "",
                "state_class": str(state_class) if state_class else "",
            }
        )
        looks_like_energy = (
            str(device_class or "") == "energy"
            and str(state_class or "") == "total_increasing"
        ) or is_energy_unit(str(unit) if unit else None)
        if looks_like_energy:
            entities.append(
                {
                    "entity_id": state.entity_id,
                    "name": str(attributes.get("friendly_name") or state.entity_id),
                    "unit": str(unit) if unit else "",
                }
            )

    entities.sort(key=lambda item: item["entity_id"])
    entity_ids.sort(key=lambda item: item["entity_id"])
    registry = er.async_get(hass)
    names = {
        entry_.entity_id: entry_.name or entry_.original_name
        for entry_ in registry.entities.values()
        if entry_.entity_id in {item["entity_id"] for item in entity_ids}
    }
    for item in entity_ids:
        item["registry_name"] = names.get(item["entity_id"]) or ""

    connection.send_result(
        msg["id"],
        {
            "energy_sources": entities[: MAX_CHOICES * 4],
            "entities": entity_ids[: MAX_CHOICES * 8],
            "units": ["kWh", "Wh", "MWh", "GWh", "MJ", "GJ"],
            "modes": list(DERIVED_MODES),
            "mode_options": list(MODE_OPTIONS),
            "scan_scopes": list(SCAN_SCOPES),
            "default_scan_scope": DEFAULT_SCAN_SCOPE,
            "sections": list(SECTIONS),
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_REVIEW})
@websocket_api.async_response
async def ws_review(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Return the current scan result and the recent actions."""
    entry, error = _entry_or_error(hass, msg, msg.get("entry_id"))
    if error is not None:
        connection.send_error(*error)
        return
    hub = get_hub(hass, entry.entry_id)
    if hub is None:  # pragma: no cover - defensive
        connection.send_error(*_error(msg["id"], "Energy Guard is still starting up."))
        return
    coordinator_data = hub.coordinator.data if hub.coordinator is not None else None
    connection.send_result(
        msg["id"],
        {
            "candidates": [dict(item) for item in hub.scan_candidates],
            "cost_suggestions": list(
                getattr(coordinator_data, "cost_suggestions", []) or []
            ),
            "scan_warnings": list(getattr(coordinator_data, "warnings", []) or []),
            "last_scan": hub.last_scan.isoformat() if hub.last_scan else None,
            "last_scan_scope": hub.last_scan_scope,
            "last_scan_statistic_count": hub.last_scan_statistic_count,
            "last_scan_error": hub.last_scan_error,
            "repairs": [dict(item) for item in list(hub.repairs)[-20:]],
            "calibrations": [dict(item) for item in list(hub.calibrations)[-20:]],
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_FILES})
@websocket_api.async_response
async def ws_files(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Return the backup and report files (names and sizes, never paths)."""
    entry, error = _entry_or_error(hass, msg, msg.get("entry_id"))
    if error is not None:
        connection.send_error(*error)
        return
    hub = get_hub(hass, entry.entry_id)
    if hub is None:  # pragma: no cover - defensive
        connection.send_error(*_error(msg["id"], "Energy Guard is still starting up."))
        return

    def _listing(directory: Path) -> list[dict[str, Any]]:
        if not directory.exists():
            return []
        files = []
        for path in sorted(directory.glob("*.json"), reverse=True):
            try:
                stat = path.stat()
            except OSError:  # pragma: no cover - race with a deletion
                continue
            files.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                }
            )
        return files[:50]

    connection.send_result(
        msg["id"],
        {
            "backups": _listing(hub.backup_dir),
            "reports": _listing(hub.report_dir),
            "note": (
                "Only file names and sizes are returned; the location of the "
                "directory is never exposed."
            ),
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_TEMPLATES})
@websocket_api.async_response
async def ws_templates(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Return the generated YAML copy of the configuration."""
    entry, error = _entry_or_error(hass, msg, msg.get("entry_id"))
    if error is not None:
        connection.send_error(*error)
        return
    options = dict(entry.options)
    from .models import SensorDefinition, UtilityMeterDefinition

    protected = [
        SensorDefinition.from_dict(item) for item in options.get(CONF_PROTECTED) or []
    ]
    derived = [
        SensorDefinition.from_dict(item) for item in options.get(CONF_DERIVED) or []
    ]
    meters = [
        UtilityMeterDefinition.from_dict(item)
        for item in options.get(CONF_UTILITY_METERS) or []
    ]
    connection.send_result(
        msg["id"],
        {
            "yaml": render_templates(protected, derived, meters),
            "note": (
                "This YAML is a copy of your Energy Guard definitions. Energy Guard "
                "never edits any YAML file."
            ),
        },
    )


# ---------------------------------------------------------------------------
# write commands
# ---------------------------------------------------------------------------
@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_SET,
        vol.Required("section"): vol.In(list(SECTIONS)),
        vol.Required("data"): dict,
        vol.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_set(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Merge a validated settings section into the options."""
    entry, error = _entry_or_error(hass, msg, msg.get("entry_id"))
    if error is not None:
        connection.send_error(*error)
        return
    section = str(msg["section"])
    if not is_settings_section(section):
        connection.send_error(
            *_error(
                msg["id"],
                f"Section '{section}' holds definitions; use {WS_DEFINITION}.",
            )
        )
        return
    try:
        await async_set_section(hass, entry, section, dict(msg["data"]))
    except ConfigError as err:
        connection.send_error(*_error(msg["id"], str(err)))
        return
    _notify_subscribers(hass, entry)
    connection.send_result(
        msg["id"],
        {
            "config": _config(hass, entry),
            "section": section,
            "stored_under": section_key(section),
            "reloaded": True,
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_DEFINITION,
        vol.Required("section"): vol.In(list(SECTIONS)),
        vol.Required("action"): vol.In(["add", "update", "toggle", "delete"]),
        # Never "id": that key is the WebSocket message id of the envelope.
        vol.Optional("definition_id"): str,
        vol.Optional("data"): dict,
        vol.Optional("confirm", default=False): bool,
        vol.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_definition(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Add, update, toggle or delete one definition."""
    entry, error = _entry_or_error(hass, msg, msg.get("entry_id"))
    if error is not None:
        connection.send_error(*error)
        return
    section = str(msg["section"])
    if not is_definition_section(section):
        connection.send_error(
            *_error(msg["id"], f"Section '{section}' does not hold definitions.")
        )
        return
    try:
        item = await async_definition_action(
            hass,
            entry,
            section,
            str(msg["action"]),
            definition=dict(msg.get("data") or {}),
            item_id=msg.get("definition_id"),
            confirm=bool(msg.get("confirm", False)),
        )
    except ConfigError as err:
        connection.send_error(*_error(msg["id"], str(err)))
        return
    _notify_subscribers(hass, entry)
    connection.send_result(
        msg["id"],
        {
            "config": _config(hass, entry),
            "section": section,
            "action": msg["action"],
            "item": item,
            "reloaded": True,
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_SUBSCRIBE})
@websocket_api.async_response
async def ws_subscribe(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Subscribe to configuration changes made through this API."""
    entry, error = _entry_or_error(hass, msg, msg.get("entry_id"))
    if error is not None:
        connection.send_error(*error)
        return
    subscribers: dict[str, list[tuple[int, Any]]] = hass.data.setdefault(
        DOMAIN, {}
    ).setdefault(_SUBSCRIBERS, {})
    subscribers.setdefault(entry.entry_id, []).append((msg["id"], connection))

    @callback
    def _unsubscribe() -> None:
        subscribers[entry.entry_id] = [
            (msg_id, conn)
            for msg_id, conn in subscribers.get(entry.entry_id, [])
            if conn is not connection
        ]

    connection.subscriptions[msg["id"]] = _unsubscribe
    connection.send_result(msg["id"])


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------
@callback
def async_register_websocket_api(hass: HomeAssistant) -> None:
    """Register the configuration WebSocket API (idempotent)."""
    if hass.data.setdefault(DOMAIN, {}).get(_REGISTERED):
        return
    websocket_api.async_register_command(hass, ws_get)
    websocket_api.async_register_command(hass, ws_choices)
    websocket_api.async_register_command(hass, ws_review)
    websocket_api.async_register_command(hass, ws_files)
    websocket_api.async_register_command(hass, ws_templates)
    websocket_api.async_register_command(hass, ws_set)
    websocket_api.async_register_command(hass, ws_definition)
    websocket_api.async_register_command(hass, ws_subscribe)
    hass.data[DOMAIN][_REGISTERED] = True
    _LOGGER.debug("Energy Guard configuration WebSocket API registered")


@callback
def async_unregister_websocket_api(hass: HomeAssistant) -> None:
    """Forget the registration flag (called when the last entry unloads)."""
    hass.data.setdefault(DOMAIN, {}).pop(_REGISTERED, None)


__all__ = [
    "WS_CHOICES",
    "WS_DEFINITION",
    "WS_EVENT",
    "WS_FILES",
    "WS_GET",
    "WS_REVIEW",
    "WS_SET",
    "WS_SUBSCRIBE",
    "WS_TEMPLATES",
    "async_register_websocket_api",
    "async_unregister_websocket_api",
]
