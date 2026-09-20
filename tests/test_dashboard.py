"""Dashboard contract tests: the shipped Lovelace dashboard stays safe and real.

The dashboard in ``dashboards/energy_guard.yaml`` is the only UI artifact Energy
Guard ships, and it is deliberately read-only.  These tests fail loudly when:

* the YAML stops parsing or loses a view,
* it references an Energy Guard entity that does not exist after setup,
* a card would call a data-changing service,
* one of its Jinja templates raises,
* the example entities drift away from the README quick start,
* the documentation stops linking the dashboard.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template

from custom_components.energy_guard.const import DOMAIN, SCAN_SCOPES, SERVICE_NAMES
from custom_components.energy_guard.costs import cost_suggestion
from custom_components.energy_guard.detection import fingerprint
from custom_components.energy_guard.models import ScanCandidate

from .conftest import SOURCE, protected_definition, set_source, setup_guard

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_PATH = REPO_ROOT / "dashboards" / "energy_guard.yaml"
README_PATH = REPO_ROOT / "README.md"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"
DOC_PATH = REPO_ROOT / "docs" / "DASHBOARD.md"

# Only read-only services may be offered by a card.  Repairs, calibration and
# clearing must stay manual, confirmation-gated service calls (SAFETY.md).
READ_ONLY_ACTIONS = {"energy_guard.scan_statistics"}
# Services a card must never trigger: they change statistics or other
# integration's entities.  They may only be printed as manual instructions.
DATA_CHANGING_ACTIONS = {
    "energy_guard.repair_statistics",
    "energy_guard.calibrate_utility_meter",
    "energy_guard.clear_statistics",
}
# Read-only, but still manual: they write files into the config directory.
MANUAL_ONLY_ACTIONS = DATA_CHANGING_ACTIONS | {
    "energy_guard.export_repair_report",
    "energy_guard.export_templates",
}

KNOWN_CARD_TYPES = {
    "button",
    "conditional",
    "entities",
    "history-graph",
    "horizontal-stack",
    "markdown",
    "statistics-graph",
}


@pytest.fixture(autouse=True)
def recorder(recorder_mock):
    """Run every test in this module against an in-memory recorder."""
    return recorder_mock


@pytest.fixture(name="dashboard")
def dashboard_fixture() -> dict[str, Any]:
    """Return the parsed dashboard."""
    return yaml.safe_load(DASHBOARD_PATH.read_text())


def _walk(node: Any):
    """Yield every node of a nested YAML structure."""
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _entity_ids(node: Any) -> set[str]:
    """Return every entity id the dashboard refers to."""
    found: set[str] = set()
    for item in _walk(node):
        if not isinstance(item, dict):
            continue
        entity = item.get("entity")
        if isinstance(entity, str):
            found.add(entity)
        entities = item.get("entities")
        if isinstance(entities, list):
            for entry in entities:
                if isinstance(entry, str):
                    found.add(entry)
    return found


def _card_actions(node: Any) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(service, action data)`` for every service a card would call."""
    calls: list[tuple[str, dict[str, Any]]] = []
    for item in _walk(node):
        if not isinstance(item, dict) or "action" not in item:
            continue
        service = item.get("perform-action") or item.get("service")
        if isinstance(service, str):
            calls.append((service, item))
    return calls


async def _render(hass: HomeAssistant, text: str) -> str:
    """Render one dashboard template.

    ``Template.async_render`` is synchronous in Home Assistant 2026.9 (it
    returned an awaitable in older releases), so handle both instead of pinning
    the test to one implementation detail.
    """
    result = Template(text, hass).async_render(parse_result=False)
    if inspect.isawaitable(result):
        result = await result
    return str(result)


def _markdown(node: Any) -> list[str]:
    """Return the ``content`` of every markdown card."""
    return [
        item["content"]
        for item in _walk(node)
        if isinstance(item, dict)
        and item.get("type") == "markdown"
        and isinstance(item.get("content"), str)
    ]


# ---------------------------------------------------------------------------
# structure
# ---------------------------------------------------------------------------
def test_dashboard_is_valid_yaml_with_the_expected_views(dashboard) -> None:
    """The dashboard parses and keeps its three documented views."""
    assert dashboard["title"] == "Energy Guard"
    views = dashboard["views"]
    assert [view["title"] for view in views] == [
        "Status",
        "Sources vs protected",
        "Repair centre",
    ]
    paths = [view["path"] for view in views]
    assert len(set(paths)) == len(paths)
    for view in views:
        assert view["cards"], view["title"]
        for card in view["cards"]:
            assert card["type"] in KNOWN_CARD_TYPES, card["type"]


def test_dashboard_only_offers_read_only_actions(dashboard) -> None:
    """No card may trigger a data-changing service."""
    calls = _card_actions(dashboard)
    assert calls, "the dashboard should offer the read-only scan"
    for service, action in calls:
        assert service in READ_ONLY_ACTIONS, service
        assert service not in MANUAL_ONLY_ACTIONS, service
        # Even the read-only scan is confirmation-gated, so a stray click on a
        # wall tablet cannot fire it silently.
        assert action.get("confirmation"), service

    # Every scan button asks for a real scope, and all three scopes are offered.
    scopes = {
        (action.get("data") or {}).get("scope")
        for service, action in calls
        if service == "energy_guard.scan_statistics"
    }
    assert scopes == set(SCAN_SCOPES), scopes

    # The manual procedures are documentation only: they appear as text with the
    # required confirmations, never as a clickable action.
    text = "\n".join(_markdown(dashboard))
    for service in sorted(
        DATA_CHANGING_ACTIONS | {"energy_guard.export_repair_report"}
    ):
        assert service in text, service
    assert "confirm: true" in text
    assert "confirm_cost: true" in text
    assert "dry_run: true" in text
    # A monetary repair is always shown together with its own confirmation.
    assert text.index("confirm_cost: true") < text.index(
        "energy_guard.clear_statistics"
    )


# ---------------------------------------------------------------------------
# entities
# ---------------------------------------------------------------------------
async def test_dashboard_references_real_energy_guard_entities(
    hass: HomeAssistant, dashboard
) -> None:
    """Every Energy Guard entity in the dashboard exists after setup."""
    set_source(hass, SOURCE, 38243.46)
    await setup_guard(
        hass,
        protected=[protected_definition()],
        cost={"enabled": True, "price": 0.235, "currency": "GEL"},
    )
    await hass.async_block_till_done()

    referenced = _entity_ids(dashboard)
    owned = {
        entity_id
        for entity_id in referenced
        if entity_id.split(".", 1)[-1].startswith("energy_guard")
    }
    assert owned, "the dashboard must show the Energy Guard diagnostics"
    missing = sorted(
        entity_id for entity_id in owned if hass.states.get(entity_id) is None
    )
    assert not missing, f"dashboard references unknown entities: {missing}"

    # The example entities of the quick start are referenced too - they must be
    # real entity ids of the documented example, not typos.
    assert SOURCE in referenced
    assert "sensor.grid_import_protected" in referenced
    assert "sensor.grid_import_monthly" in referenced


def test_dashboard_example_entities_match_the_readme(dashboard) -> None:
    """Example entities come from the README quick start, not from nowhere."""
    readme = README_PATH.read_text()
    external = {
        entity_id
        for entity_id in _entity_ids(dashboard)
        if not entity_id.split(".", 1)[-1].startswith("energy_guard")
    }
    assert external
    for entity_id in sorted(external):
        assert entity_id in readme, entity_id


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------
async def test_dashboard_templates_render_with_real_states(hass: HomeAssistant) -> None:
    """Every Jinja template in the dashboard renders, with and without issues."""
    set_source(hass, SOURCE, 38243.46)
    await setup_guard(hass, protected=[protected_definition()])
    await hass.async_block_till_done()

    dashboard = yaml.safe_load(DASHBOARD_PATH.read_text())
    templates = _markdown(dashboard)

    # 1. Clean state: the real Energy Guard entities are already live.
    for text in templates:
        assert await _render(hass, text)

    # 2. Open issue: feed the diagnostic sensors the payload the hub would
    #    publish, including a real scan candidate and cost suggestion.
    start_time = datetime(2026, 9, 20, 4, 0, tzinfo=UTC)
    candidate = ScanCandidate(
        statistic_id="sensor.grid_import_protected",
        start_time=start_time,
        detected_at=datetime(2026, 9, 20, 9, 0, tzinfo=UTC),
        unit="kWh",
        offset=-38243.46,
        observed_delta=38243.46,
        expected_delta=0.0,
        estimated_false_energy=38243.46,
        evidence=["zero_then_restore", "state_flat_while_sum_grew"],
        severity="error",
    )
    suggestion = cost_suggestion(
        cost_statistic_id="sensor.grid_import_protected_cost",
        start_time=start_time,
        energy_offset=candidate.offset,
        price=0.235,
        currency="GEL",
        fingerprint=fingerprint(
            "sensor.grid_import_protected_cost", start_time, -8987.21, "GEL"
        ),
        energy_statistic_id=candidate.statistic_id,
    )
    assert suggestion["worked_example"]  # the dashboard prints this line

    hass.states.async_set(
        "sensor.energy_guard_statistics_issues",
        "1",
        {
            "last_scan": "2026-09-20T09:00:00+00:00",
            "last_scan_error": None,
            "recorder_available": True,
            "lookback_hours": 48,
            "statistics_jump_threshold": 100.0,
            "scan_warnings": [],
            "candidates": [candidate.to_dict()],
            "cost_repair_suggestions": [suggestion],
            "cost_repair": {
                "enabled": True,
                "price": 0.235,
                "effective_price": 0.235,
                "price_entity_id": None,
                "currency": "GEL",
                "energy_statistic_id": "sensor.grid_import_protected",
                "cost_statistic_id": "sensor.grid_import_protected_cost",
            },
            "repair_hint": "Nothing is repaired automatically.",
        },
    )
    hass.states.async_set(
        "binary_sensor.energy_guard_data_issue",
        "on",
        {
            "window_hours": 24,
            "anomaly_count": 2,
            "blocked_events": 1,
            "statistics_issues": 1,
            "estimated_false_energy": 38243.46,
            "affected_entities": ["sensor.grid_import_protected"],
            "last_anomaly": {
                "kind": "zero_then_restore",
                "severity": "error",
                "timestamp": "2026-09-20T04:00:00+00:00",
                "message": "Source reported 0.0 kWh during an outage.",
            },
            "simultaneous_failures": [],
            "last_scan": "2026-09-20T09:00:00+00:00",
            "action_required": "Run energy_guard.scan_statistics.",
        },
    )

    rendered = [await _render(hass, text) for text in templates]
    joined = "\n".join(rendered)

    assert "Something needs your review" in joined
    assert "zero_then_restore" in joined
    assert "sensor.grid_import_protected" in joined  # candidate table
    assert suggestion["worked_example"] in joined  # cost table
    assert "confirm_cost: true" in joined  # the manual procedure


# ---------------------------------------------------------------------------
# documentation
# ---------------------------------------------------------------------------
def test_dashboard_is_documented() -> None:
    """README, docs and CHANGELOG point at the dashboard."""
    assert DOC_PATH.exists()
    doc = DOC_PATH.read_text()
    assert "dashboards/energy_guard.yaml" in doc
    assert "Raw configuration editor" in doc
    assert "read-only" in doc

    readme = README_PATH.read_text()
    assert "dashboards/energy_guard.yaml" in readme
    assert "docs/DASHBOARD.md" in readme

    assert "dashboard" in CHANGELOG_PATH.read_text().lower()


def test_dashboard_does_not_mention_unknown_services(dashboard) -> None:
    """Documented service names in the dashboard are real service names."""
    text = "\n".join(_markdown(dashboard))
    mentioned = {
        part.split()[0]
        for part in text.replace("`", " ").split()
        if part.startswith("energy_guard.")
    }
    assert mentioned
    for name in sorted(mentioned):
        assert name.split(".", 1)[1] in SERVICE_NAMES, name
    assert DOMAIN in text
