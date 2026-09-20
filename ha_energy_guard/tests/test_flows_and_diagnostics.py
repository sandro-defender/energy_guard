"""Config flow, options flow, diagnostics and Repairs issue tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_guard.const import DOMAIN
from custom_components.energy_guard.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.energy_guard.hub import get_hub
from custom_components.energy_guard.migrations import CURRENT_VERSION
from custom_components.energy_guard.repairs import (
    ISSUE_BLOCKED_READINGS,
    ISSUE_STATISTICS_OFFSET,
)
from custom_components.energy_guard.statistics import async_repair_statistics

from .conftest import SOURCE, protected_definition, set_source, setup_guard

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def recorder(recorder_mock):
    """Run every test in this module against an in-memory recorder."""
    return recorder_mock


async def test_config_flow_creates_protected_sensor(hass: HomeAssistant) -> None:
    """The full config flow run creates an entry with a protected sensor."""
    set_source(hass, SOURCE, 38243.46)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"source_entity_id": [SOURCE]}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "naming"

    # A name whose entity id is already taken is refused (Home Assistant would
    # otherwise silently create "sensor.grid_import_2").
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name_0": "Grid import"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "naming"
    assert result["errors"] == {"name_0": "name_in_use"}

    # The default name keeps the "protected" suffix and works.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name_0": "Grid import protected"}
    )
    assert result["step_id"] == "detection"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"scan_interval": 300, "lookback_hours": 48}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    protected = entry.options["protected_sensors"]
    assert len(protected) == 1
    assert protected[0]["source_entity_id"] == SOURCE
    assert protected[0]["name"] == "Grid import protected"
    assert entry.options["detection_rules"]["scan_interval"] == 300

    # The entity configured through the UI is live and guarding immediately.
    await hass.async_block_till_done()
    state = hass.states.get("sensor.grid_import_protected")
    assert state is not None
    assert float(state.state) == pytest.approx(38243.46)
    assert state.attributes["device_class"] == "energy"
    assert state.attributes["state_class"] == "total_increasing"
    assert state.attributes["unit_of_measurement"] == "kWh"

    # A reconnect (0 after a good value) is held: never a false zero.
    set_source(hass, SOURCE, 0.0)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.grid_import_protected").state == "unavailable"

    set_source(hass, SOURCE, 38243.46)
    await hass.async_block_till_done()
    assert float(
        hass.states.get("sensor.grid_import_protected").state
    ) == pytest.approx(38243.46)

    # A second flow is refused: Energy Guard is single instance.
    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "single_instance_allowed"


async def test_config_flow_rejects_non_energy_source(hass: HomeAssistant) -> None:
    """Only cumulative energy sensors can be protected."""
    hass.states.async_set(
        "sensor.not_energy",
        "12",
        {"state_class": "measurement", "unit_of_measurement": "%"},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"source_entity_id": ["sensor.not_energy"]}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_sources"}


async def test_options_flow_detection_and_yaml_export(hass: HomeAssistant) -> None:
    """The options flow edits detection rules and exports templates."""
    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(hass, protected=[protected_definition()])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"

    # Detection rules form -> saved through the reload-aware options flow.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "detection_rules"}
    )
    assert result["step_id"] == "detection_rules"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 120}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["detection_rules"]["scan_interval"] == 120

    # The YAML export stays read-only unless write_file is chosen.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "export_yaml"}
    )
    assert result["step_id"] == "export_yaml"

    assert "templates.yaml" in result["description_placeholders"]["path"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"write": False}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "yaml_not_written"

    # Explicitly asking for the file writes into the config dir, not the repo.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "export_yaml"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"write": True}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "yaml_written"
    exported = Path(result["description_placeholders"]["path"])
    content = await hass.async_add_executor_job(
        lambda: exported.read_text(encoding="utf-8") if exported.exists() else ""
    )
    assert "state_class: total_increasing" in content
    assert "sensor.grid_import" in content
    assert hass.config.config_dir in str(exported)


async def test_diagnostics_does_not_leak_paths(hass: HomeAssistant) -> None:
    """Diagnostics contain the configuration but no filesystem paths."""
    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(hass, protected=[protected_definition()])

    payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["integration"]["domain"] == DOMAIN
    assert payload["configuration"]["protected_sensors"][0]["id"] == "test_grid_import"
    assert payload["entities"]["test_grid_import"] == "sensor.grid_import_protected"
    assert payload["runtime"]["backup_directory"] == "energy_guard/backups"
    assert payload["runtime"]["report_directory"] == "energy_guard/reports"
    assert hass.config.config_dir not in str(payload)
    assert "password" not in str(payload).lower()
    assert "recorder.db" not in str(payload)


async def test_repairs_issue_for_statistics_candidates(hass: HomeAssistant) -> None:
    """A suspicious statistics offset raises a (non fixable) Repairs issue."""
    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(hass, protected=[protected_definition()])
    hub = get_hub(hass)

    # Simulate what the coordinator stores after a scan.
    hub.scan_candidates = [
        {
            "statistic_id": "sensor.grid_import",
            "start_time": "2026-09-19T12:00:00+00:00",
            "offset": -38243.46,
            "unit": "kWh",
            "severity": "error",
        }
    ]
    from custom_components.energy_guard.repairs import async_sync_repair_issues

    await async_sync_repair_issues(hass, hub)

    registry = ir.async_get(hass)
    issue_id = f"{ISSUE_STATISTICS_OFFSET}_{entry.entry_id}"
    assert registry.async_get_issue(DOMAIN, issue_id) is not None
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue.is_fixable is False
    assert issue.translation_placeholders["count"] == "1"
    assert issue.translation_placeholders["statistic_id"] == "sensor.grid_import"

    # No candidates -> the issue disappears again.
    hub.scan_candidates = []
    await async_sync_repair_issues(hass, hub)
    assert registry.async_get_issue(DOMAIN, issue_id) is None

    # Blocked readings raise the second issue.
    from homeassistant.util import dt as dt_util

    from custom_components.energy_guard.const import SEVERITY_WARNING
    from custom_components.energy_guard.models import AnomalyEvent

    hub.log_event(
        AnomalyEvent(
            kind="zero_reset_blocked",
            severity=SEVERITY_WARNING,
            timestamp=dt_util.utcnow(),
            source_entity=SOURCE,
            protected_entity="sensor.grid_import_protected",
            estimated_false_energy=38243.46,
            unit="kWh",
            message="blocked",
        )
    )
    await async_sync_repair_issues(hass, hub)
    blocked = registry.async_get_issue(
        DOMAIN, f"{ISSUE_BLOCKED_READINGS}_{entry.entry_id}"
    )
    assert blocked is not None
    assert blocked.translation_placeholders["count"] == "1"
    assert blocked.translation_placeholders["entities"] == SOURCE


async def test_unload_removes_issues_and_services(hass: HomeAssistant) -> None:
    """Unloading the entry cleans up Repairs issues and services."""
    set_source(hass, SOURCE, 38243.46)
    entry = await setup_guard(hass, protected=[protected_definition()])

    assert hass.services.has_service(DOMAIN, "scan_statistics")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.services.has_service(DOMAIN, "scan_statistics")
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"{ISSUE_STATISTICS_OFFSET}_{entry.entry_id}"
        )
        is None
    )


async def test_backups_are_written_outside_the_repository(hass: HomeAssistant) -> None:
    """Backups live in the config dir, never in the integration folder."""
    set_source(hass, SOURCE, 38243.46)
    await setup_guard(hass, protected=[protected_definition()])
    hub = get_hub(hass)

    assert str(hub.backup_dir).startswith(hass.config.config_dir)
    assert hub.backup_dir.name == "backups"
    assert str(REPO_ROOT) not in str(hub.backup_dir)

    # A repair of an unknown statistic never writes anything.
    report = await async_repair_statistics(
        hass,
        requests=[],
        confirm=True,
        dry_run=False,
        create_backup=True,
        verify=False,
        backup_dir=hub.backup_dir,
    )
    assert report.status == "preview"
    backups = await hass.async_add_executor_job(
        lambda: sorted(hub.backup_dir.glob("*.json")) if hub.backup_dir.exists() else []
    )
    assert backups == []


async def test_services_wrap_several_entries_in_config_entries(
    hass: HomeAssistant,
) -> None:
    """With more than one entry, service responses are wrapped consistently."""
    second_source = "sensor.second_import"
    set_source(hass, SOURCE, 38243.46)
    set_source(hass, second_source, 100.0)
    await setup_guard(hass, protected=[protected_definition()])

    second = MockConfigEntry(
        domain=DOMAIN,
        title="Energy Guard 2",
        data={},
        options={
            "protected_sensors": [
                protected_definition(
                    source=second_source,
                    name="Second import protected",
                    id="test_second_import",
                )
            ],
            "derived_sensors": [],
            "utility_meters": [],
            "detection_rules": {},
            "cost_repair": {},
        },
        version=CURRENT_VERSION,
        entry_id="test_entry_id_2",
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    scan = await hass.services.async_call(
        DOMAIN, "scan_statistics", {}, blocking=True, return_response=True
    )
    assert scan["read_only"] is True
    assert len(scan["config_entries"]) == 2
    assert {item["statistic_ids"][0] for item in scan["config_entries"]} == {
        "sensor.grid_import_protected",
        "sensor.second_import_protected",
    }

    report = await hass.services.async_call(
        DOMAIN,
        "export_repair_report",
        {"name": "two_entries"},
        blocking=True,
        return_response=True,
    )
    assert len(report["config_entries"]) == 2
    assert all(item["json_file"] for item in report["config_entries"])

    # With a single entry named explicitly the flat response is returned again.
    single = await hass.services.async_call(
        DOMAIN,
        "scan_statistics",
        {"config_entry_id": second.entry_id},
        blocking=True,
        return_response=True,
    )
    assert "config_entries" not in single
    # The protected entity and its source are both scanned for the one entry.
    assert "sensor.second_import_protected" in single["statistic_ids"]
    assert "sensor.second_import" in single["statistic_ids"]
