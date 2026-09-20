"""Shared configuration layer for the options flow and the configuration panel.

Both user interfaces - Home Assistant's options flow and the Energy Guard
sidebar panel - must validate and store configuration in exactly the same way.
This module is that single place:

* the section names and where each section is stored,
* the schema (from :mod:`selectors`) that validates a payload,
* the name/entity-id collision rules,
* writing the new options and reloading the config entry.

Nothing here changes data.  Everything it writes is *configuration*; statistics
repairs, meter calibration and clearing keep their own confirmation-gated
services.
"""

from __future__ import annotations

from typing import Any, Final

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_BACKUPS,
    CONF_COST,
    CONF_DEFINITION_SECTIONS,
    CONF_DERIVED,
    CONF_DETECTION,
    CONF_ENABLED,
    CONF_ID,
    CONF_NAME,
    CONF_PROTECTED,
    CONF_SCALE,
    CONF_SETTINGS_SECTIONS,
    CONF_UTILITY_METERS,
    SECTION_BACKUPS,
    SECTION_COST,
    SECTION_DERIVED,
    SECTION_DETECTION,
    SECTION_METERS,
    SECTION_PROTECTED,
    SECTION_STATISTICS,
    SECTIONS,
)
from .selectors import (
    backups_schema,
    cost_schema,
    derived_schema,
    detection_schema,
    entity_id_for,
    name_in_use,
    protected_schema,
    statistics_schema,
    utility_meter_schema,
)

#: Schemas used to validate a payload of each section.
SECTION_SCHEMAS: Final = {
    SECTION_PROTECTED: protected_schema,
    SECTION_DERIVED: derived_schema,
    SECTION_METERS: utility_meter_schema,
    SECTION_DETECTION: detection_schema,
    SECTION_STATISTICS: statistics_schema,
    SECTION_COST: cost_schema,
    SECTION_BACKUPS: backups_schema,
}

#: Options key each section is stored under.
SECTION_KEYS: Final = {
    SECTION_PROTECTED: CONF_PROTECTED,
    SECTION_DERIVED: CONF_DERIVED,
    SECTION_METERS: CONF_UTILITY_METERS,
    SECTION_DETECTION: CONF_DETECTION,
    # The statistics thresholds live in the detection section, exactly like in
    # the options flow (they are detection rules).
    SECTION_STATISTICS: CONF_DETECTION,
    SECTION_COST: CONF_COST,
    SECTION_BACKUPS: CONF_BACKUPS,
}

#: Definition keys that may not be set by a client (managed by Energy Guard).
MANAGED_KEYS: Final = (CONF_ID,)


class ConfigError(ValueError):
    """Raised when a configuration payload is invalid.

    The message is written for a human and is shown in the panel, so it never
    contains a traceback, a file path, a token or a database location.
    """


def is_definition_section(section: str) -> bool:
    """Return True when ``section`` stores a list of definitions."""
    return section in CONF_DEFINITION_SECTIONS


def is_settings_section(section: str) -> bool:
    """Return True when ``section`` stores a settings dictionary."""
    return section in CONF_SETTINGS_SECTIONS


def section_key(section: str) -> str:
    """Return the options key a section is stored under."""
    if section not in SECTION_KEYS:
        raise ConfigError(f"Unknown configuration section '{section}'.")
    return SECTION_KEYS[section]


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------
def validate_section(section: str, data: dict[str, Any]) -> dict[str, Any]:
    """Validate a settings payload of ``section`` and return the cleaned data."""
    if section not in SECTION_KEYS:
        raise ConfigError(f"Unknown configuration section '{section}'.")
    if is_definition_section(section):
        raise ConfigError(
            f"Section '{section}' holds definitions; use the definition actions."
        )
    schema = SECTION_SCHEMAS[section]()
    try:
        validated = schema(dict(data))
    except vol.Invalid as err:
        raise ConfigError(_describe(err)) from err
    return clean_optional(validated)


def clean_optional(payload: dict[str, Any]) -> dict[str, Any]:
    """Turn empty strings into ``None`` and parse the numeric optionals.

    Kept behaviour-compatible with the options flow: an empty optional value
    must become ``None`` (or stay absent) instead of an empty string.
    """
    from .const import CONF_BASELINE_AT, CONF_CYCLE, CONF_MAX_VALUE, CONF_PRICE_ENTITY

    cleaned = dict(payload)
    for key in (CONF_MAX_VALUE, CONF_BASELINE_AT, CONF_CYCLE, CONF_PRICE_ENTITY):
        value = cleaned.get(key)
        if value in ("", None):
            if key == CONF_MAX_VALUE:
                cleaned[key] = None
        elif key == CONF_MAX_VALUE:
            try:
                cleaned[key] = float(value)
            except (TypeError, ValueError):
                cleaned[key] = None
    return cleaned


def taken_entity_ids(
    options: dict[str, Any], payload: dict[str, Any], *, ignore_id: str | None = None
) -> list[str]:
    """Return entity ids a new/edited definition must not collide with.

    Every entity the definition reads (its source, sources, total or parts) and
    every other protected/derived sensor is taken: a name that matches one of
    them would be renamed with a ``_2`` suffix by Home Assistant instead of
    being used as-is, which is easy to pick by mistake in the Energy Dashboard.
    """
    from .const import CONF_PARTS, CONF_SOURCE, CONF_SOURCES, CONF_TOTAL

    taken: list[str] = []
    for key in (CONF_SOURCE, CONF_TOTAL):
        value = payload.get(key)
        if value:
            taken.append(str(value))
    for key in (CONF_SOURCES, CONF_PARTS):
        taken.extend(str(item) for item in (payload.get(key) or []))
    for section in (CONF_PROTECTED, CONF_DERIVED):
        for item in options.get(section) or []:
            if ignore_id is not None and item.get(CONF_ID) == ignore_id:
                continue
            taken.append(entity_id_for(str(item.get(CONF_NAME) or "")))
    return taken


def _describe(err: vol.Invalid) -> str:
    """Return a short, path-aware message for a schema error."""
    path = ".".join(str(part) for part in err.path) or "value"
    return f"{path}: {err.msg}"


def build_definition(
    section: str,
    payload: dict[str, Any],
    *,
    options: dict[str, Any],
    definition_id: str | None = None,
) -> dict[str, Any]:
    """Validate one definition and return it ready for storage.

    ``definition_id`` keeps the identity of an edited definition, which is what
    keeps existing entity ids and unique ids stable.
    """
    if not is_definition_section(section):
        raise ConfigError(f"Section '{section}' does not hold definitions.")
    schema = SECTION_SCHEMAS[section]()
    item = dict(payload)
    item.pop(CONF_ID, None)
    try:
        validated = schema(_with_defaults(section, item))
    except vol.Invalid as err:
        raise ConfigError(_describe(err)) from err

    item = clean_optional(validated)
    name = str(item.get(CONF_NAME) or "").strip()
    if not name:
        raise ConfigError("A name is required.")
    if name_in_use(name, taken_entity_ids(options, item, ignore_id=definition_id)):
        raise ConfigError(
            f"'{name}' would create the entity id "
            f"{entity_id_for(name)}, which is already taken. Pick another name "
            "(for example with the ' protected' suffix)."
        )
    item[CONF_NAME] = name
    if definition_id is not None:
        item[CONF_ID] = definition_id
    if section == SECTION_PROTECTED:
        item.setdefault(CONF_SCALE, 1.0)
    return item


def _with_defaults(section: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Fill in the required fields a definition of ``section`` must carry.

    The forms of the options flow always submit every field, so the schema can
    be strict.  A WebSocket client may omit them, which would make the sensor
    unusable, so the same keys are defaulted here.
    """
    from .const import (
        CONF_ACCEPT_RESET,
        CONF_CONFIRM_SCANS,
        CONF_DO_NOT_DECREASE,
        CONF_ENABLED,
        CONF_LARGE_JUMP,
        CONF_LARGE_JUMP_RATIO,
        CONF_MODE,
        CONF_PRECISION,
        CONF_RECOVERY_HOLD_SCANS,
        CONF_REJECT_LARGE_JUMPS,
        CONF_REQUIRE_ALL_SOURCES,
        CONF_TARGET_UNIT,
        CONF_ZERO_MIN_PREVIOUS,
        DEFAULT_ACCEPT_RESET,
        DEFAULT_CONFIRM_SCANS,
        DEFAULT_DO_NOT_DECREASE,
        DEFAULT_LARGE_JUMP,
        DEFAULT_LARGE_JUMP_RATIO,
        DEFAULT_PRECISION,
        DEFAULT_RECOVERY_HOLD_SCANS,
        DEFAULT_REJECT_LARGE_JUMPS,
        DEFAULT_UNIT,
        DEFAULT_ZERO_MIN_PREVIOUS,
        MODE_SUM,
    )

    defaults: dict[str, Any]
    if section == SECTION_PROTECTED:
        defaults = {
            CONF_ENABLED: True,
            CONF_TARGET_UNIT: DEFAULT_UNIT,
            CONF_DO_NOT_DECREASE: DEFAULT_DO_NOT_DECREASE,
            CONF_ACCEPT_RESET: DEFAULT_ACCEPT_RESET,
            CONF_ZERO_MIN_PREVIOUS: DEFAULT_ZERO_MIN_PREVIOUS,
            CONF_CONFIRM_SCANS: DEFAULT_CONFIRM_SCANS,
            CONF_RECOVERY_HOLD_SCANS: DEFAULT_RECOVERY_HOLD_SCANS,
            CONF_LARGE_JUMP: DEFAULT_LARGE_JUMP,
            CONF_LARGE_JUMP_RATIO: DEFAULT_LARGE_JUMP_RATIO,
            CONF_REJECT_LARGE_JUMPS: DEFAULT_REJECT_LARGE_JUMPS,
            CONF_PRECISION: DEFAULT_PRECISION,
        }
    elif section == SECTION_DERIVED:
        # The derived schema carries no precision field (the model default is
        # used), so it must not be sent either.
        defaults = {
            CONF_ENABLED: True,
            CONF_MODE: MODE_SUM,
            CONF_TARGET_UNIT: DEFAULT_UNIT,
            CONF_REQUIRE_ALL_SOURCES: True,
            CONF_DO_NOT_DECREASE: DEFAULT_DO_NOT_DECREASE,
            CONF_SCALE: 1.0,
        }
    elif section == SECTION_METERS:
        defaults = {CONF_ENABLED: True}
    else:  # pragma: no cover - defensive, callers check the section first
        defaults = {}
    merged = dict(defaults)
    merged.update(payload)
    return merged


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------
@callback
def _store_options(hass: HomeAssistant, entry: ConfigEntry, options: dict[str, Any]):
    """Write the options and reload the entry (never blocking the event loop)."""
    hass.config_entries.async_update_entry(entry, options=options)
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_set_section(
    hass: HomeAssistant, entry: ConfigEntry, section: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Merge a validated settings payload into the options and reload."""
    validated = validate_section(section, data)
    options = dict(entry.options)
    key = section_key(section)
    current = dict(options.get(key) or {})
    current.update(validated)
    options[key] = current
    _store_options(hass, entry, options)
    return current


async def async_definition_action(
    hass: HomeAssistant,
    entry: ConfigEntry,
    section: str,
    action: str,
    *,
    definition: dict[str, Any] | None = None,
    item_id: str | None = None,
    confirm: bool = False,
) -> dict[str, Any] | None:
    """Add, update, toggle or delete one definition.

    Returns the stored definition (``None`` for a delete).  Deleting needs
    ``confirm: true`` - the same rule the options flow applies.
    """
    if not is_definition_section(section):
        raise ConfigError(f"Section '{section}' does not hold definitions.")
    key = section_key(section)
    options = dict(entry.options)
    items = [dict(item) for item in (options.get(key) or [])]

    if action == "add":
        item = build_definition(section, definition or {}, options=options)
        import uuid

        item[CONF_ID] = uuid.uuid4().hex
        items.append(item)
    else:
        if not item_id:
            raise ConfigError("An id is required for this action.")
        index = next(
            (pos for pos, item in enumerate(items) if item.get(CONF_ID) == item_id),
            None,
        )
        if index is None:
            raise ConfigError("This definition no longer exists.")
        current = items[index]
        if action == "update":
            item = build_definition(
                section,
                definition or {},
                options=options,
                definition_id=item_id,
            )
            items[index] = item
        elif action == "toggle":
            item = dict(current)
            item[CONF_ENABLED] = not bool(current.get(CONF_ENABLED, True))
            items[index] = item
        elif action == "delete":
            if not confirm:
                raise ConfigError(
                    "Deleting a definition needs an explicit confirmation."
                )
            items.pop(index)
            item = None
        else:
            raise ConfigError(f"Unknown action '{action}'.")

    options[key] = items
    _store_options(hass, entry, options)
    return item


__all__ = [
    "MANAGED_KEYS",
    "SECTIONS",
    "SECTION_KEYS",
    "SECTION_SCHEMAS",
    "ConfigError",
    "async_definition_action",
    "async_set_section",
    "build_definition",
    "clean_optional",
    "is_definition_section",
    "is_settings_section",
    "section_key",
    "taken_entity_ids",
    "validate_section",
]
