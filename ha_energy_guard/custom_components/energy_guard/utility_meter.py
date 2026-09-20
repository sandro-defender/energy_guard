"""Utility meter calibration.

``utility_meter`` entities keep their own value, so a false kWh offset that was
already counted by a meter does not disappear when the statistics are repaired.
This module calibrates a meter either to an exact value or to a value computed
from a protected cumulative source minus a cycle-start baseline.

Calibration uses the official ``utility_meter.calibrate`` entity service, is
previewed unless ``confirm: true`` is passed and verifies the new value
afterwards.  No other integration's entities are ever touched.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import ALREADY_REPAIRED_TOLERANCE
from .protect import parse_float
from .recorder_io import (
    as_utc,
    async_statistics_rows,
    recorder_is_available,
)
from .units import convert

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utility meter calibration
# ---------------------------------------------------------------------------
async def async_source_value_at(
    hass: HomeAssistant, entity_id: str, when: datetime
) -> float | None:
    """Return the recorded value of an entity at (or just before) ``when``."""
    if not recorder_is_available(hass):
        state = hass.states.get(entity_id)
        if state is None:
            return None
        from .protect import parse_float

        return parse_float(state.state)
    start = when - timedelta(hours=2)
    rows = (await async_statistics_rows(hass, [entity_id], start, when)).get(
        entity_id, []
    )
    for row in reversed(rows):
        if row.get("state") is not None:
            return float(row["state"])
    state = hass.states.get(entity_id)
    if state is None:
        return None
    from .protect import parse_float

    return parse_float(state.state)


async def async_calibrate_utility_meter(
    hass: HomeAssistant,
    *,
    entity_id: str,
    value: float | None,
    source_entity_id: str | None,
    baseline_value: float | None,
    cycle_start: datetime | None,
    cycle: str | None,
    confirm: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """Calibrate a utility meter to an exact value (preview by default)."""

    state = hass.states.get(entity_id)
    if state is None:
        raise ServiceValidationError(
            f"Entity '{entity_id}' was not found. Provide the entity id of an "
            "existing utility_meter sensor."
        )
    registry_entry = None
    try:
        from homeassistant.helpers import entity_registry as er

        registry_entry = er.async_get(hass).async_get(entity_id)
    except Exception:
        registry_entry = None
    if registry_entry is not None and registry_entry.platform != "utility_meter":
        raise ServiceValidationError(
            f"'{entity_id}' is provided by the '{registry_entry.platform}' "
            "integration. energy_guard.calibrate_utility_meter only calibrates "
            "utility_meter entities."
        )

    current_value = parse_float(state.state)
    unit = state.attributes.get("unit_of_measurement") or "kWh"

    source_value: float | None = None
    baseline = baseline_value
    if source_entity_id:
        source_state = hass.states.get(source_entity_id)
        if source_state is None:
            raise ServiceValidationError(
                f"Source entity '{source_entity_id}' was not found."
            )
        source_value = parse_float(source_state.state)
        if source_value is None:
            raise ServiceValidationError(
                f"Source entity '{source_entity_id}' currently reports "
                f"'{source_state.state}', which cannot be used as a calibration "
                "baseline. Wait until the sensor reports a number again."
            )
    if cycle_start is not None and baseline_value is None and source_entity_id:
        recorded = await async_source_value_at(
            hass, source_entity_id, as_utc(cycle_start)
        )
        if recorded is None:
            raise ServiceValidationError(
                f"No recorded value for '{source_entity_id}' at "
                f"{as_utc(cycle_start).isoformat()}. Provide baseline_value instead."
            )
        baseline = recorded

    target = value
    if target is None:
        if source_value is None:
            raise ServiceValidationError(
                "Provide either 'value' (the exact meter value) or "
                "'source_entity_id' (a cumulative source to calculate it from)."
            )
        target = source_value - (baseline or 0.0)
    target = round(float(target), 6)

    source_unit = None
    if source_entity_id:
        source_unit = hass.states.get(source_entity_id).attributes.get(
            "unit_of_measurement"
        )
        if source_unit and source_unit != unit:
            target = round(convert(target, source_unit, unit), 6)

    delta = None if current_value is None else round(target - current_value, 6)
    result: dict[str, Any] = {
        "entity_id": entity_id,
        "unit": unit,
        "current_value": current_value,
        "target_value": target,
        "delta": delta,
        "source_entity_id": source_entity_id,
        "source_value": source_value,
        "baseline_value": baseline,
        "cycle_start": as_utc(cycle_start).isoformat() if cycle_start else None,
        "cycle": cycle,
        "confirmed": confirm,
        "applied": False,
    }

    if not confirm or dry_run:
        result["status"] = "preview"
        result["message"] = (
            f"Preview only: would calibrate {entity_id} from {current_value} to "
            f"{target} {unit}. Re-run with confirm: true and dry_run: false to apply."
        )
        return result

    if abs(target - (current_value or 0.0)) < ALREADY_REPAIRED_TOLERANCE:
        result["status"] = "already_correct"
        result["message"] = (
            f"{entity_id} already is at {current_value} {unit}; nothing to calibrate."
        )
        return result

    try:
        await hass.services.async_call(
            "utility_meter",
            "calibrate",
            {"value": target},
            target={"entity_id": entity_id},
            blocking=True,
        )
    except HomeAssistantError as err:
        result["status"] = "failed"
        result["message"] = (
            f"Calibration failed: {err}. Check that the utility_meter integration is "
            "loaded and that the entity id is correct."
        )
        return result

    await hass.async_block_till_done()
    new_state = hass.states.get(entity_id)
    from .protect import parse_float as _parse

    new_value = _parse(new_state.state) if new_state else None
    result["applied"] = True
    result["new_value"] = new_value
    result["verified"] = (
        None
        if new_value is None
        else abs(new_value - target) < max(ALREADY_REPAIRED_TOLERANCE, 0.01)
    )
    result["status"] = "calibrated"
    result["message"] = (
        f"Calibrated {entity_id} to {target} {unit} (was {current_value})."
        if result["verified"] is not False
        else (
            f"The calibrate service ran but {entity_id} reports {new_value} instead of "
            f"{target}. Check the utility_meter cycle configuration."
        )
    )
    return result
