"""Repairs issues raised by Energy Guard.

Energy Guard never changes data on its own, so the repair flows in the Home
Assistant UI are *not* fixable by the integration: they point at the data that
needs attention and tell the user which service call (with ``confirm: true``)
would repair it.  That keeps the "read-only until explicitly confirmed" rule
while still surfacing problems on the Repairs dashboard.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, NAME, REPOSITORY_URL, VERSION
from .hub import EnergyGuardHub

_LOGGER = logging.getLogger(__name__)

ISSUE_STATISTICS_OFFSET = "statistics_offset"
ISSUE_BLOCKED_READINGS = "blocked_readings"

LEARN_MORE_URL = f"{REPOSITORY_URL}#readme"


async def async_sync_repair_issues(hass: HomeAssistant, hub: EnergyGuardHub) -> None:
    """Create or remove the Repairs issues for one Energy Guard entry."""
    candidates = hub.scan_candidates or []
    if candidates:
        latest = candidates[0]
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{ISSUE_STATISTICS_OFFSET}_{hub.entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_STATISTICS_OFFSET,
            translation_placeholders={
                "count": str(len(candidates)),
                "statistic_id": str(latest.get("statistic_id", "unknown")),
                "start_time": str(latest.get("start_time", "unknown")),
                "offset": str(latest.get("offset", "unknown")),
                "unit": str(latest.get("unit") or ""),
            },
            learn_more_url=LEARN_MORE_URL,
        )
    else:
        ir.async_delete_issue(
            hass, DOMAIN, f"{ISSUE_STATISTICS_OFFSET}_{hub.entry.entry_id}"
        )

    if hub.anomaly_count > 0:
        events = hub.events_since(hub.issue_window_start)
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{ISSUE_BLOCKED_READINGS}_{hub.entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_BLOCKED_READINGS,
            translation_placeholders={
                "integration": NAME,
                "version": VERSION,
                "count": str(hub.anomaly_count),
                "window_hours": str(hub.config.detection.issue_window_hours),
                "estimated_false_energy": f"{hub.estimated_false_energy:g}",
                "entities": ", ".join(
                    sorted(
                        {
                            str(event.source_entity)
                            for event in events
                            if event.source_entity
                        }
                    )[:10]
                ),
            },
            learn_more_url=LEARN_MORE_URL,
        )
    else:
        ir.async_delete_issue(
            hass, DOMAIN, f"{ISSUE_BLOCKED_READINGS}_{hub.entry.entry_id}"
        )


async def async_delete_repair_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Remove all Repairs issues of a config entry (used on unload)."""
    for issue in (ISSUE_STATISTICS_OFFSET, ISSUE_BLOCKED_READINGS):
        ir.async_delete_issue(hass, DOMAIN, f"{issue}_{entry_id}")
