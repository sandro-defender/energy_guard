"""Dashboard data tests: the numbers behind the panel home.

``dashboard.build_dashboard`` aggregates the protection totals, the activity
points, the recent events, the scan state and the managed counts from one hub.
It is strictly read-only; these tests pin the aggregation rules (what counts
as "blocked", which windows apply, newest-first ordering).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.energy_guard.const import (
    EVENT_DECREASE_BLOCKED,
    EVENT_SOURCE_RECOVERED,
    EVENT_ZERO_RESET_BLOCKED,
    SEVERITY_INFO,
    SEVERITY_WARNING,
)
from custom_components.energy_guard.dashboard import (
    BLOCKED_KINDS,
    DASHBOARD_DAYS,
    RECENT_EVENTS,
    build_dashboard,
    kind_label,
)
from custom_components.energy_guard.hub import FAILURE_KINDS, get_hub
from custom_components.energy_guard.models import AnomalyEvent

from .conftest import SOURCE, protected_definition, set_source, setup_guard


@pytest.fixture(autouse=True)
def recorder(recorder_mock):
    """Run every test in this module against an in-memory recorder."""
    return recorder_mock


def _event(
    kind: str, *, hours_ago: float = 1, energy: float | None = None, message="m"
) -> AnomalyEvent:
    """Return an event with the same source (no simultaneous detection)."""
    return AnomalyEvent(
        kind=kind,
        severity=SEVERITY_WARNING,
        timestamp=dt_util.utcnow() - timedelta(hours=hours_ago),
        source_entity=SOURCE,
        estimated_false_energy=energy,
        message=message,
    )


async def test_blocked_kinds_are_the_protection_engine_failures() -> None:
    """ "Blocked" is exactly the set the protection engine reports as failures."""
    assert BLOCKED_KINDS == FAILURE_KINDS
    assert EVENT_ZERO_RESET_BLOCKED in BLOCKED_KINDS
    assert EVENT_SOURCE_RECOVERED not in BLOCKED_KINDS


async def test_dashboard_counts_protection_totals(hass: HomeAssistant) -> None:
    """Blocked readings and false energy are totalled, with a 24 h split."""
    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(hass, protected=[protected_definition()])
    hub = get_hub(hass, entry.entry_id)
    assert hub is not None

    hub.log_event(_event(EVENT_ZERO_RESET_BLOCKED, hours_ago=2, energy=10.0))
    hub.log_event(_event(EVENT_DECREASE_BLOCKED, hours_ago=30, energy=5.5))
    hub.log_event(
        AnomalyEvent(
            kind=EVENT_SOURCE_RECOVERED,
            severity=SEVERITY_INFO,
            timestamp=dt_util.utcnow() - timedelta(hours=1),
            source_entity=SOURCE,
            message="back",
        )
    )

    dashboard = build_dashboard(hub)
    protection = dashboard["protection"]
    assert protection["blocked_total"] == 2
    assert protection["blocked_24h"] == 1
    assert protection["false_kwh_total"] == 15.5
    assert protection["false_kwh_24h"] == 10.0
    assert protection["events_total"] == 3
    assert dashboard["window_days"] == DASHBOARD_DAYS


async def test_dashboard_points_cover_two_weeks(hass: HomeAssistant) -> None:
    """Points and per-kind counts only cover the chart window."""
    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(hass, protected=[protected_definition()])
    hub = get_hub(hass, entry.entry_id)
    assert hub is not None

    hub.log_event(_event(EVENT_ZERO_RESET_BLOCKED, hours_ago=1, energy=2.0))
    hub.log_event(_event(EVENT_ZERO_RESET_BLOCKED, hours_ago=20 * 24, energy=100.0))

    dashboard = build_dashboard(hub)
    assert len(dashboard["points"]) == 1
    assert dashboard["points"][0]["blocked"] is True
    assert dashboard["points"][0]["energy"] == 2.0
    assert dashboard["by_kind"] == [
        {
            "kind": EVENT_ZERO_RESET_BLOCKED,
            "label": kind_label(EVENT_ZERO_RESET_BLOCKED),
            "count": 1,
        }
    ]
    # ... while the totals still cover everything in the log.
    assert dashboard["protection"]["blocked_total"] == 2
    assert dashboard["protection"]["false_kwh_total"] == 102.0


async def test_dashboard_recent_events_are_newest_first_and_capped(
    hass: HomeAssistant,
) -> None:
    """The activity feed shows the newest events first, with short previews."""
    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(hass, protected=[protected_definition()])
    hub = get_hub(hass, entry.entry_id)
    assert hub is not None

    for index in range(RECENT_EVENTS + 2):
        hub.log_event(
            _event(
                EVENT_ZERO_RESET_BLOCKED,
                hours_ago=RECENT_EVENTS + 1 - index,
                message=f"event {index} " + "x" * 200,
            )
        )

    recent = build_dashboard(hub)["recent"]
    assert len(recent) == RECENT_EVENTS
    assert recent[0]["message"].startswith(f"event {RECENT_EVENTS + 1} ")
    assert len(recent[0]["message"]) <= 160
    assert recent[0]["label"] == kind_label(EVENT_ZERO_RESET_BLOCKED)
    assert recent[0]["severity"] == SEVERITY_WARNING
    assert recent[0]["source"] == SOURCE
    assert recent[0]["t"] >= recent[-1]["t"]


async def test_dashboard_reports_scan_state_counts_and_actions(
    hass: HomeAssistant,
) -> None:
    """Scan state, managed counts and the action log are exposed read-only."""
    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(
        hass,
        protected=[
            protected_definition(),
            protected_definition(
                source="sensor.other", name="Other protected", enabled=False
            ),
        ],
        derived=[{"id": "d1", "name": "Sum", "enabled": True}],
        utility_meters=[
            {
                "id": "m1",
                "name": "Monthly",
                "utility_meter_entity_id": "sensor.monthly",
                "enabled": False,
            }
        ],
    )
    hub = get_hub(hass, entry.entry_id)
    assert hub is not None

    scanned = dt_util.utcnow() - timedelta(minutes=5)
    hub.last_scan = scanned
    hub.last_scan_scope = "energy"
    hub.last_scan_statistic_count = 42
    hub.scan_candidates = [{"statistic_id": SOURCE}]
    hub.record_repair({"kind": "repair", "statistic_id": SOURCE})
    hub.record_calibration({"entity_id": "sensor.monthly"})

    dashboard = build_dashboard(hub)
    assert dashboard["scan"] == {
        "last_scan": scanned.isoformat(),
        "scope": "energy",
        "statistic_count": 42,
        "error": None,
        "candidates": 1,
    }
    assert dashboard["counts"] == {
        "protected": 2,
        "protected_enabled": 1,
        "derived": 1,
        "derived_enabled": 1,
        "meters": 1,
        "meters_enabled": 0,
    }
    assert dashboard["actions"] == {"repairs": 1, "calibrations": 1}
    assert dashboard["generated_at"]


async def test_kind_label_falls_back_to_the_kind() -> None:
    """Unknown kinds render as readable words, never crash the feed."""
    assert kind_label(EVENT_ZERO_RESET_BLOCKED) == "False zero blocked"
    assert kind_label("some_future_kind") == "some future kind"
