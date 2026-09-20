"""Binary sensor platform: one entity that says "something needs attention".

``binary_sensor.energy_guard_data_issue`` is the single "is my Energy Dashboard
data trustworthy right now?" flag.  It turns on when a statistics scan found a
suspicious offset or when a blocked reading was severe enough to warn, and its
attributes explain which sensors are affected (see ``hub.has_open_issues``).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SEVERITY_ERROR, SEVERITY_WARNING
from .entity import EnergyGuardDiagnosticEntity
from .hub import EnergyGuardHub

_LOGGER = logging.getLogger(__name__)

NAME_DATA_ISSUE = "Energy Guard Data issue"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Energy Guard binary sensor."""
    hub: EnergyGuardHub = hass.data[DOMAIN]["hubs"][entry.entry_id]
    async_add_entities([EnergyGuardDataIssueBinarySensor(hub)], update_before_add=False)


class EnergyGuardDataIssueBinarySensor(EnergyGuardDiagnosticEntity, BinarySensorEntity):
    """Turns on when Energy Guard sees a data problem worth acting on.

    The sensor is deliberately *not* marked as a diagnostic entity so that it
    shows up prominently on a dashboard or in an automation.
    """

    _attr_name = NAME_DATA_ISSUE
    _attr_unique_id = f"{DOMAIN}_data_issue"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = None

    @property
    def is_on(self) -> bool:
        """Return True when there is an open issue."""
        return self.hub.has_open_issues()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return why the sensor is on."""
        last_event = self.hub.last_event
        window = self.hub.config.detection.issue_window_hours
        problem_events = [
            event
            for event in self.hub.events_since(self.hub.issue_window_start)
            if event.severity in (SEVERITY_WARNING, SEVERITY_ERROR)
        ]
        affected = sorted(
            {
                event.source_entity or event.protected_entity
                for event in problem_events
                if event.source_entity or event.protected_entity
            }
        )
        return {
            "window_hours": window,
            "anomaly_count": len(problem_events),
            "blocked_events": sum(
                1
                for event in problem_events
                if event.kind
                in ("zero_reset_blocked", "decrease_blocked", "large_jump")
            ),
            "statistics_issues": len(self.hub.scan_candidates),
            "estimated_false_energy": self.hub.estimated_false_energy,
            "affected_entities": affected,
            "last_anomaly": last_event.to_dict() if last_event is not None else None,
            "simultaneous_failures": [
                event.to_dict()
                for event in problem_events
                if event.kind == "simultaneous_anomaly"
            ],
            "last_scan": self.hub.last_scan.isoformat() if self.hub.last_scan else None,
            "action_required": (
                "Run energy_guard.scan_statistics to review the suspicious statistics "
                "offsets. Repairs only happen when you call "
                "energy_guard.repair_statistics with confirm: true."
                if self.hub.scan_candidates
                else "No action required: Energy Guard blocked the bad reading before "
                "it could reach the statistics."
            ),
        }
