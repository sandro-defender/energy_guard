"""Dashboard data for the Energy Guard configuration panel.

The panel opens on a dashboard home (status hero, statistics, graphs and recent
activity) instead of a bare form.  This module aggregates everything that view
needs from one hub.  It is strictly read-only: it only reads the in-memory
diagnostic log, the scan state and the configuration.

Honesty rules (the panel repeats them in a footnote):

* totals cover the diagnostic log, which is bounded (at most 250 events, pruned
  after the configured retention) - they are labelled "logged", never absolute,
* repairs and calibrations come from the persisted action log,
* per-day series are plain event points; the browser buckets them into *local*
  days, so no timezone is guessed here.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, Final

from homeassistant.util import dt as dt_util

from .const import (
    EVENT_ABOVE_MAX,
    EVENT_CALIBRATED,
    EVENT_CLEARED,
    EVENT_DECREASE_BLOCKED,
    EVENT_LARGE_JUMP,
    EVENT_REPAIRED,
    EVENT_RESET_ACCEPTED,
    EVENT_RESTORED,
    EVENT_SIMULTANEOUS,
    EVENT_SOURCE_INVALID,
    EVENT_SOURCE_RECOVERED,
    EVENT_STATISTICS_OFFSET,
    EVENT_ZERO_RESET_BLOCKED,
)
from .hub import FAILURE_KINDS

if TYPE_CHECKING:
    from .hub import EnergyGuardHub

#: Days of activity points the dashboard charts cover.
DASHBOARD_DAYS: Final = 14
#: Events shown in the recent-activity feed.
RECENT_EVENTS: Final = 8
#: Hours of the "last 24 hours" comparison window.
DAY_HOURS: Final = 24

#: Kinds that mean "a reading was held back and never reached the statistics".
#: This is exactly the set the protection engine reports as failures.
BLOCKED_KINDS: Final = FAILURE_KINDS

#: Short human labels for the event kinds, used by the charts and the feed.
KIND_LABELS: Final = {
    EVENT_ZERO_RESET_BLOCKED: "False zero blocked",
    EVENT_DECREASE_BLOCKED: "Decrease blocked",
    EVENT_SOURCE_INVALID: "Source invalid",
    EVENT_SOURCE_RECOVERED: "Source recovered",
    EVENT_LARGE_JUMP: "Large jump",
    EVENT_RESET_ACCEPTED: "Reset accepted",
    EVENT_ABOVE_MAX: "Above maximum",
    EVENT_SIMULTANEOUS: "Simultaneous failures",
    EVENT_STATISTICS_OFFSET: "Statistics offset",
    EVENT_REPAIRED: "Statistics repaired",
    EVENT_CALIBRATED: "Meter calibrated",
    EVENT_CLEARED: "Statistics cleared",
    EVENT_RESTORED: "Value restored",
}

#: Message length kept for the activity feed (the full text stays in the log).
MESSAGE_PREVIEW_LENGTH: Final = 160


def kind_label(kind: str) -> str:
    """Return the short human label of an event kind."""
    return KIND_LABELS.get(kind, kind.replace("_", " "))


def build_dashboard(hub: EnergyGuardHub) -> dict[str, Any]:
    """Aggregate the dashboard home of one hub (read-only).

    The returned dictionary is JSON serialisable and shaped for the panel:

    * ``protection`` - logged blocked readings and false energy (total + 24h),
    * ``points`` - one entry per logged event of the last 14 days, bucketed
      into local days by the browser,
    * ``by_kind`` - logged event counts per kind, for the donut chart,
    * ``recent`` - the newest events first, for the activity feed,
    * ``scan`` - the state of the last statistics scan,
    * ``counts`` - managed definitions and how many are enabled,
    * ``actions`` - repairs and calibrations in the persisted action log.
    """
    now = dt_util.utcnow()
    day_ago = now - timedelta(hours=DAY_HOURS)
    window_start = now - timedelta(days=DASHBOARD_DAYS)

    events = list(hub.events)
    blocked_total = 0
    blocked_24h = 0
    false_total = 0.0
    false_24h = 0.0
    points: list[dict[str, Any]] = []
    by_kind: dict[str, int] = {}
    for event in events:
        blocked = event.kind in BLOCKED_KINDS
        energy = event.estimated_false_energy or 0.0
        if blocked:
            blocked_total += 1
            false_total += energy
            if event.timestamp >= day_ago:
                blocked_24h += 1
                false_24h += energy
        if event.timestamp >= window_start:
            by_kind[event.kind] = by_kind.get(event.kind, 0) + 1
            points.append(
                {
                    "t": event.timestamp.isoformat(),
                    "blocked": blocked,
                    "energy": round(energy, 3) if blocked else 0.0,
                }
            )

    recent = [
        {
            "t": event.timestamp.isoformat(),
            "severity": event.severity,
            "kind": event.kind,
            "label": kind_label(event.kind),
            "message": event.message[:MESSAGE_PREVIEW_LENGTH],
            "source": event.source_entity,
        }
        for event in events[-RECENT_EVENTS:][::-1]
    ]

    protected = list(hub.config.protected)
    derived = list(hub.config.derived)
    meters = list(hub.config.utility_meters)

    return {
        "generated_at": now.isoformat(),
        "window_days": DASHBOARD_DAYS,
        "protection": {
            "blocked_total": blocked_total,
            "blocked_24h": blocked_24h,
            "false_kwh_total": round(false_total, 3),
            "false_kwh_24h": round(false_24h, 3),
            "events_total": len(events),
        },
        "points": points,
        "by_kind": [
            {"kind": kind, "label": kind_label(kind), "count": count}
            for kind, count in sorted(by_kind.items(), key=lambda item: -item[1])
        ],
        "recent": recent,
        "scan": {
            "last_scan": hub.last_scan.isoformat() if hub.last_scan else None,
            "scope": hub.last_scan_scope,
            "statistic_count": hub.last_scan_statistic_count,
            "error": hub.last_scan_error,
            "candidates": len(hub.scan_candidates),
        },
        "counts": {
            "protected": len(protected),
            "protected_enabled": sum(1 for item in protected if item.enabled),
            "derived": len(derived),
            "derived_enabled": sum(1 for item in derived if item.enabled),
            "meters": len(meters),
            "meters_enabled": sum(1 for item in meters if item.enabled),
        },
        "actions": {
            "repairs": len(hub.repairs),
            "calibrations": len(hub.calibrations),
        },
    }


__all__ = [
    "BLOCKED_KINDS",
    "DASHBOARD_DAYS",
    "DAY_HOURS",
    "KIND_LABELS",
    "MESSAGE_PREVIEW_LENGTH",
    "RECENT_EVENTS",
    "build_dashboard",
    "kind_label",
]
