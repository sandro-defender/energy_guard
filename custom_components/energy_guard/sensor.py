"""Sensor platform: protected, derived and diagnostic entities.

Three kinds of entities are created here:

* protected/derived energy sensors (their logic lives in :mod:`entity`),
* dashboard sensors that explain the state of the integration
  (``sensor.energy_guard_last_anomaly``, ``..._estimated_false_energy``,
  ``..._statistics_issues``, ``..._last_scan``, ``..._anomalies``),
* nothing else - Energy Guard never wraps or replaces foreign entities.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
)
from .costs import effective_price
from .entity import (
    EnergyGuardDerivedSensor,
    EnergyGuardDiagnosticEntity,
    EnergyGuardProtectedSensor,
)
from .hub import EnergyGuardHub

_LOGGER = logging.getLogger(__name__)

# Diagnostic entity names (= entity ids, see entity.py).
NAME_LAST_ANOMALY = "Energy Guard Last anomaly"
NAME_ANOMALIES = "Energy Guard Anomalies"
NAME_FALSE_ENERGY = "Energy Guard Estimated false energy"
NAME_STATISTICS_ISSUES = "Energy Guard Statistics issues"
NAME_LAST_SCAN = "Energy Guard Last scan"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Energy Guard sensors."""
    hub: EnergyGuardHub = hass.data[DOMAIN]["hubs"][entry.entry_id]
    entities: list[SensorEntity] = []

    for definition in hub.config.protected:
        if not definition.enabled:
            continue
        entities.append(EnergyGuardProtectedSensor(hub, definition))

    for definition in hub.config.derived:
        if not definition.enabled:
            continue
        if not definition.all_sources:
            _LOGGER.warning(
                "Energy Guard derived sensor '%s' has no sources and was skipped",
                definition.name,
            )
            continue
        entities.append(EnergyGuardDerivedSensor(hub, definition))

    entities.extend(
        [
            EnergyGuardLastAnomalySensor(hub),
            EnergyGuardAnomalyCountSensor(hub),
            EnergyGuardFalseEnergySensor(hub),
            EnergyGuardStatisticsIssuesSensor(hub),
            EnergyGuardLastScanSensor(hub),
        ]
    )

    async_add_entities(entities, update_before_add=False)


def _short_state(value: float | None, unit: str, precision: int = 3) -> str:
    """Return a compact human readable value for attributes."""
    if value is None:
        return "unknown"
    return f"{round(value, precision)} {unit}".strip()


class EnergyGuardLastAnomalySensor(EnergyGuardDiagnosticEntity, SensorEntity):
    """Shows the most recent anomaly detected by Energy Guard."""

    _attr_name = NAME_LAST_ANOMALY
    _attr_unique_id = f"{DOMAIN}_last_anomaly"

    @property
    def native_value(self) -> str:
        """Return the kind of the last anomaly."""
        event = self.hub.last_event
        return event.kind if event is not None else "none"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the details of the last anomaly."""
        event = self.hub.last_event
        if event is None:
            return {"status": "no anomalies recorded"}
        payload = event.to_dict()
        payload["anomaly_count"] = self.hub.anomaly_count
        payload["estimated_false_energy"] = self.hub.estimated_false_energy
        payload["statistics_issues"] = len(self.hub.scan_candidates)
        return payload

    @property
    def icon(self) -> str:
        """Return a state dependent icon."""
        event = self.hub.last_event
        if event is None:
            return "mdi:shield-check"
        if event.severity == SEVERITY_ERROR:
            return "mdi:shield-alert"
        if event.severity == SEVERITY_WARNING:
            return "mdi:shield-sync-outline"
        return "mdi:shield-check"


class EnergyGuardAnomalyCountSensor(EnergyGuardDiagnosticEntity, SensorEntity):
    """Number of anomalies inside the configured issue window."""

    _attr_name = NAME_ANOMALIES
    _attr_unique_id = f"{DOMAIN}_anomalies"
    _attr_icon = "mdi:alert-decagram-outline"
    _attr_native_unit_of_measurement = "anomalies"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        """Return the number of anomalies."""
        return self.hub.anomaly_count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return window information and a compact event list."""
        window = self.hub.config.detection.issue_window_hours
        recent = [
            {
                "timestamp": event.timestamp.isoformat(),
                "kind": event.kind,
                "severity": event.severity,
                "source_entity": event.source_entity,
                "protected_entity": event.protected_entity,
                "previous_value": event.previous_value,
                "invalid_value": event.invalid_value,
                "restored_value": event.restored_value,
                "estimated_false_energy": event.estimated_false_energy,
                "unit": event.unit,
            }
            for event in self.hub.events_since(self.hub.issue_window_start)
        ]
        return {
            "window_hours": window,
            "events": recent[-20:],
            "blocked_events": sum(
                1
                for event in self.hub.events_since(self.hub.issue_window_start)
                if event.kind
                in ("zero_reset_blocked", "decrease_blocked", "large_jump")
            ),
        }


class EnergyGuardFalseEnergySensor(EnergyGuardDiagnosticEntity, SensorEntity):
    """Estimated false energy that Energy Guard blocked."""

    _attr_name = NAME_FALSE_ENERGY
    _attr_unique_id = f"{DOMAIN}_estimated_false_energy"
    # A windowed estimate, not a cumulative meter: keep it a measurement and
    # leave the device class unset (energy + measurement is not a valid pair).
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:calculator-variant-outline"

    @property
    def native_value(self) -> float:
        """Return the estimated false energy in kWh."""
        return self.hub.estimated_false_energy

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the window the estimate refers to."""
        return {
            "window_hours": self.hub.config.detection.issue_window_hours,
            "last_anomaly": (
                self.hub.last_event.kind if self.hub.last_event is not None else None
            ),
            "note": (
                "This is the energy Energy Guard refused to publish. Statistics that "
                "were already written before Energy Guard was installed are listed by "
                "sensor.energy_guard_statistics_issues and must be repaired explicitly."
            ),
        }


class EnergyGuardStatisticsIssuesSensor(EnergyGuardDiagnosticEntity, SensorEntity):
    """Number of suspicious statistics offsets found by the last scan."""

    _attr_name = NAME_STATISTICS_ISSUES
    _attr_unique_id = f"{DOMAIN}_statistics_issues"
    _attr_icon = "mdi:database-alert-outline"
    _attr_native_unit_of_measurement = "issues"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        """Return the number of suspicious statistics offsets."""
        return len(self.hub.scan_candidates)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the scan result details."""
        data = self.coordinator.data
        suggestions = list(getattr(data, "cost_suggestions", []) or [])
        cost = self.hub.config.cost
        return {
            "last_scan": self.hub.last_scan.isoformat() if self.hub.last_scan else None,
            "last_scan_error": self.hub.last_scan_error,
            "recorder_available": getattr(data, "recorder_available", True),
            "lookback_hours": self.hub.config.detection.lookback_hours,
            "statistics_jump_threshold": self.hub.config.detection.statistics_jump_threshold,
            "scan_warnings": list(getattr(data, "warnings", []) or []),
            "candidates": self.hub.scan_candidates[:10],
            "cost_repair_suggestions": suggestions,
            "cost_repair": {
                "enabled": cost.enabled,
                "price": cost.price,
                "effective_price": effective_price(self.hass, cost),
                "price_entity_id": cost.price_entity_id,
                "currency": cost.currency,
                "energy_statistic_id": cost.energy_statistic_id,
                "cost_statistic_id": cost.cost_statistic_id,
            },
            "repair_hint": (
                "Nothing is repaired automatically. Review the candidates and call "
                "energy_guard.repair_statistics with confirm: true."
            ),
        }


class EnergyGuardLastScanSensor(EnergyGuardDiagnosticEntity, SensorEntity):
    """Timestamp of the last statistics scan."""

    _attr_name = NAME_LAST_SCAN
    _attr_unique_id = f"{DOMAIN}_last_scan"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"

    @property
    def native_value(self):
        """Return the last scan timestamp."""
        return self.hub.last_scan
