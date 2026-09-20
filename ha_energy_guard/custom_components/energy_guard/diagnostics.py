"""Diagnostics support for Energy Guard.

Diagnostics never contain tokens, passwords, database paths or other secrets -
Energy Guard does not store any.  Statistics values are included because they
are the data the integration is about, and they are the user's own data.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN, VERSION
from .hub import get_hub
from .recorder_io import recorder_is_available

TO_REDACT: set[str] = set()


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    hub = get_hub(hass, entry.entry_id)
    if hub is None:
        return {"error": "Energy Guard is not set up", "version": VERSION}

    scan = hub.coordinator.data if hub.coordinator is not None else None
    return async_redact_data(
        {
            "integration": {
                "domain": DOMAIN,
                "version": VERSION,
                "config_entry_id": entry.entry_id,
                "title": entry.title,
            },
            "configuration": hub.config.to_dict(),
            "runtime": {
                "recorder_available": recorder_is_available(hass),
                "last_scan": hub.last_scan.isoformat() if hub.last_scan else None,
                "last_scan_error": hub.last_scan_error,
                "scan_interval_seconds": hub.config.detection.scan_interval,
                "anomaly_count": hub.anomaly_count,
                "estimated_false_energy": hub.estimated_false_energy,
                "open_issues": hub.has_open_issues(),
                # Relative folder names only: diagnostics are meant to be
                # shareable, so no absolute filesystem paths are exposed.
                "backup_directory": f"{DOMAIN}/{hub.backup_dir.name}",
                "report_directory": f"{DOMAIN}/{hub.report_dir.name}",
                "generated_at": dt_util.utcnow().isoformat(),
            },
            "statistics_scan": {
                "statistic_ids": scan.statistic_ids if scan is not None else [],
                "candidates": hub.scan_candidates,
                "cost_suggestions": getattr(scan, "cost_suggestions", []) or [],
                "statistics": getattr(scan, "statistics", {}) or {},
                "warnings": getattr(scan, "warnings", []) or [],
            },
            "diagnostic_log": [event.to_dict() for event in list(hub.events)[-60:]],
            "repair_history": list(hub.repairs)[-20:],
            "calibration_history": list(hub.calibrations)[-20:],
            "entities": {
                definition.id: hub.entity_id_for(definition.id)
                for definition in hub.config.all_definitions
            },
        },
        TO_REDACT,
    )
