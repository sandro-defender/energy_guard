"""Upgrading stored configuration between Energy Guard versions.

Energy Guard stores all of its settings inside the Home Assistant **config entry
options** (an entry can also be edited by the options flow at any time).  A
migration therefore never touches files, entity ids or the recorder: it only
rewrites the option payload of an existing entry.

Rules for future changes (see also ``CONTRIBUTING.md``):

* only add or normalize keys - never silently drop a key that carries user
  intent, and never rename an entity id;
* a new setting must have a default in :mod:`custom_components.energy_guard.models`,
  so a sparse (older) payload keeps working even without a migration;
* mark renamed keys in :data:`RENAMED_KEYS` so old payloads keep their value;
* :func:`normalize_options` must stay idempotent: running it twice is a no-op;
* every migration needs a test in ``tests/test_migrations.py``.

Two layers use this module:

1. :func:`normalize_options` runs whenever an entry is loaded (so a damaged or
   sparse payload can never crash setup), and
2. :func:`async_migrate_entry` is the entry point Home Assistant calls when the
   stored entry version is older than
   :data:`custom_components.energy_guard.config_flow.EnergyGuardConfigFlow.VERSION`.
   It persists the normalized payload so the repair survives a restart.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BACKUPS,
    CONF_COST,
    CONF_DERIVED,
    CONF_DETECTION,
    CONF_PROTECTED,
    CONF_UTILITY_METERS,
)
from .models import (
    BackupConfig,
    CostRepairConfig,
    DetectionRules,
    SensorDefinition,
    UtilityMeterDefinition,
)

_LOGGER = logging.getLogger(__name__)

#: Version stored in the config entry.  Bump it (and add a step below) whenever
#: a payload that an older release wrote needs to be rewritten *and persisted*.
#: The matching flow version lives in ``config_flow.EnergyGuardConfigFlow.VERSION``.
CURRENT_VERSION = 2

#: Old key -> current key, applied to every definition payload.
RENAMED_KEYS: dict[str, str] = {
    # Kept as documentation of the intent; empty until a real rename happens.
}

#: Sections that must always exist in the stored options.
SECTIONS = (
    CONF_PROTECTED,
    CONF_DERIVED,
    CONF_UTILITY_METERS,
    CONF_DETECTION,
    CONF_COST,
    CONF_BACKUPS,
)


def _definition_payload(
    raw: Any, defaults: Any, *, section: str
) -> dict[str, Any] | None:
    """Return a complete definition payload, or None when it is unusable."""
    if not isinstance(raw, dict):
        return None
    payload = {RENAMED_KEYS.get(key, key): value for key, value in raw.items()}
    if not str(payload.get("id") or "").strip():
        _LOGGER.warning(
            "Skipping %s entry without an id: it cannot be matched to an entity",
            section,
        )
        return None
    if not str(payload.get("name") or "").strip():
        payload["name"] = str(payload["id"])
    defaults_payload = defaults.to_dict()
    # Unknown keys are dropped, missing keys keep the documented default.
    return {key: payload.get(key, value) for key, value in defaults_payload.items()}


def normalize_options(
    options: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Return the stored options in the current shape plus human readable notes.

    The function is deliberately forgiving: anything that cannot be understood
    is replaced by its default instead of raising, so a broken option payload can
    never stop the integration from loading.
    """
    notes: list[str] = []
    raw = dict(options or {})

    protected: list[dict[str, Any]] = []
    for item in raw.get(CONF_PROTECTED) or []:
        payload = _definition_payload(
            item, SensorDefinition(id="", name=""), section="protected sensor"
        )
        if payload is not None:
            protected.append(payload)
        else:
            notes.append("Dropped a protected sensor definition without an id.")
    if len(protected) != len(raw.get(CONF_PROTECTED) or []):
        _LOGGER.warning(
            "%d protected sensor definition(s) were dropped during migration",
            len(raw.get(CONF_PROTECTED) or []) - len(protected),
        )

    derived: list[dict[str, Any]] = []
    for item in raw.get(CONF_DERIVED) or []:
        payload = _definition_payload(
            item, SensorDefinition(id="", name=""), section="derived sensor"
        )
        if payload is not None:
            derived.append(payload)
        else:
            notes.append("Dropped a derived sensor definition without an id.")

    meters: list[dict[str, Any]] = []
    for item in raw.get(CONF_UTILITY_METERS) or []:
        payload = _definition_payload(
            item,
            UtilityMeterDefinition(id="", name="", utility_meter_entity_id=""),
            section="utility meter",
        )
        if payload is None:
            notes.append("Dropped a utility meter definition without an id.")
            continue
        if not str(payload.get("utility_meter_entity_id") or "").startswith("sensor."):
            # Without a target entity the meter cannot be calibrated.
            notes.append(
                f"Utility meter '{payload['id']}' has no valid utility_meter entity "
                "id and was disabled."
            )
            payload["enabled"] = False
        meters.append(payload)

    detection_raw = raw.get(CONF_DETECTION)
    if detection_raw is not None and not isinstance(detection_raw, dict):
        notes.append("Detection rules were unreadable and were reset to the defaults.")
        detection_raw = None
    detection = DetectionRules.from_dict(detection_raw).to_dict()

    cost_raw = raw.get(CONF_COST)
    if cost_raw is not None and not isinstance(cost_raw, dict):
        notes.append("Cost repair settings were unreadable and were reset.")
        cost_raw = None
    cost = CostRepairConfig.from_dict(cost_raw).to_dict()
    if cost["enabled"] and not (cost.get("energy_statistic_id")):
        notes.append(
            "Cost repair was enabled without an energy statistic and was disabled."
        )
        cost["enabled"] = False

    backups = BackupConfig.from_dict(raw.get(CONF_BACKUPS)).to_dict()

    normalized = {
        CONF_PROTECTED: protected,
        CONF_DERIVED: derived,
        CONF_UTILITY_METERS: meters,
        CONF_DETECTION: detection,
        CONF_COST: cost,
        CONF_BACKUPS: backups,
    }
    for key, value in raw.items():
        if key not in normalized:
            # Unknown top level keys are kept so a future version (or a manual
            # edit) does not lose data.
            normalized[key] = value
    return normalized, notes


def needs_migration(options: dict[str, Any] | None) -> bool:
    """Return True when the stored payload is not in the current shape."""
    raw = options or {}
    if any(section not in raw for section in SECTIONS):
        return True
    normalized, _ = normalize_options(raw)
    return normalized != {**raw}


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an older config entry to the current version.

    Returns True when the entry is usable (upgraded or already current).
    """
    if entry.version > CURRENT_VERSION:
        _LOGGER.error(
            "Energy Guard entry %s has version %s which is newer than the "
            "supported version %s. Update the integration instead of downgrading.",
            entry.entry_id,
            entry.version,
            CURRENT_VERSION,
        )
        return False

    options, notes = normalize_options(dict(entry.options))
    if entry.version == CURRENT_VERSION and not notes:
        return True

    for note in notes:
        _LOGGER.warning("Energy Guard migration: %s", note)

    hass.config_entries.async_update_entry(
        entry,
        options=options,
        version=CURRENT_VERSION,
    )
    _LOGGER.info(
        "Migrated Energy Guard entry %s from version %s to %s (%d protected, "
        "%d derived, %d utility meter(s))",
        entry.entry_id,
        entry.version,
        CURRENT_VERSION,
        len(options[CONF_PROTECTED]),
        len(options[CONF_DERIVED]),
        len(options[CONF_UTILITY_METERS]),
    )
    return True
