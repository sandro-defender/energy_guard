"""Service handlers for Energy Guard.

Safety model
------------
* ``scan_statistics``, ``export_repair_report`` and ``export_templates`` are
  strictly read-only.
* ``repair_statistics``, ``clear_statistics`` and ``calibrate_utility_meter``
  modify data (or call a modifying service) and therefore require
  ``confirm: true``.  Without it they return a preview and change nothing.
* ``repair_statistics`` only ever touches the statistic ids that were supplied
  explicitly, and it writes a JSON backup of every changed statistic first.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    EVENT_CALIBRATED,
    EVENT_CLEARED,
    EVENT_REPAIRED,
    RECOMMENDED_ACTIONS,
    SERVICE_CALIBRATE_UTILITY_METER,
    SERVICE_CLEAR_STATISTICS,
    SERVICE_EXPORT_TEMPLATES,
    SERVICE_NAMES,
    SERVICE_REPAIR_STATISTICS,
    SERVICE_REPORT,
    SERVICE_SCAN_STATISTICS,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    TEMPLATES_FILE,
)
from .costs import effective_price, tariff_line
from .detection import async_scan
from .export import (
    async_export_report,
    async_write_templates,
    build_report,
    render_templates,
)
from .hub import EnergyGuardHub, resolve_hubs
from .models import RepairRequest, build_anomaly_event
from .recorder_io import RecorderUnavailableError, as_utc
from .repairs import async_sync_repair_issues
from .statistics import async_clear_statistics, async_repair_statistics
from .utility_meter import async_calibrate_utility_meter

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
REPAIR_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required("statistic_id"): cv.string,
        vol.Required("start_time"): cv.datetime,
        vol.Required("offset"): vol.Coerce(float),
        vol.Optional("unit"): cv.string,
        vol.Optional("reason"): cv.string,
        vol.Optional("fingerprint"): cv.string,
    }
)

SCAN_SCHEMA = vol.Schema(
    {
        vol.Optional("start_time"): cv.datetime,
        vol.Optional("end_time"): cv.datetime,
        vol.Optional("statistic_ids"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("entity_ids"): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional("include_cost_suggestions", default=True): cv.boolean,
    }
)

REPAIR_SCHEMA = vol.Schema(
    {
        vol.Required("repairs"): vol.All(cv.ensure_list, [REPAIR_ITEM_SCHEMA]),
        vol.Optional("cost_repairs"): vol.All(cv.ensure_list, [REPAIR_ITEM_SCHEMA]),
        vol.Optional("confirm", default=False): cv.boolean,
        vol.Optional("confirm_cost", default=False): cv.boolean,
        vol.Optional("dry_run", default=False): cv.boolean,
        vol.Optional("create_backup", default=True): cv.boolean,
        vol.Optional("verify", default=True): cv.boolean,
        vol.Optional("config_entry_id"): cv.string,
    }
)

CALIBRATE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("value"): vol.Coerce(float),
        vol.Optional("source_entity_id"): cv.entity_id,
        vol.Optional("baseline_value"): vol.Coerce(float),
        vol.Optional("cycle_start"): cv.datetime,
        vol.Optional("cycle"): cv.string,
        vol.Optional("confirm", default=False): cv.boolean,
        vol.Optional("dry_run", default=False): cv.boolean,
        vol.Optional("config_entry_id"): cv.string,
    }
)

CLEAR_SCHEMA = vol.Schema(
    {
        vol.Required("statistic_ids"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("confirm", default=False): cv.boolean,
        vol.Optional("create_backup", default=True): cv.boolean,
        vol.Optional("config_entry_id"): cv.string,
    }
)

REPORT_SCHEMA = vol.Schema(
    {
        vol.Optional("name", default="energy_guard_report"): cv.string,
        vol.Optional("hours", default=168): vol.All(int, vol.Range(min=1, max=8760)),
        vol.Optional("config_entry_id"): cv.string,
    }
)

EXPORT_TEMPLATES_SCHEMA = vol.Schema(
    {
        vol.Optional("write_file", default=False): cv.boolean,
        vol.Optional("config_entry_id"): cv.string,
    }
)


def _cost_preview(
    hass: HomeAssistant, hub: EnergyGuardHub, cost_requests: list[RepairRequest]
) -> str:
    """Return a one line money preview for unconfirmed cost repairs."""
    cost = hub.config.cost
    price = effective_price(hass, cost)
    false_energy = (
        sum(abs(float(item.offset)) / price for item in cost_requests)
        if price
        else None
    )
    currency = cost.currency
    amounts = ", ".join(
        f"{item.statistic_id} {item.offset} {item.unit or currency or ''}".strip()
        for item in cost_requests
    )
    if false_energy is None:
        return amounts
    return f"{amounts} ({tariff_line(false_energy, price, currency)})"


def _hubs_or_fail(
    hass: HomeAssistant, call: ServiceCall, service: str
) -> list[EnergyGuardHub]:
    """Return the hubs a service call applies to."""
    hubs = resolve_hubs(hass, call.data.get("config_entry_id"))
    if not hubs:
        raise ServiceValidationError(
            f"Energy Guard is not configured (or the given config_entry_id is "
            f"unknown), so {service} cannot run. Add the integration first."
        )
    return hubs


def _repair_requests(items: list[dict[str, Any]]) -> list[RepairRequest]:
    """Convert validated service data into repair requests."""
    requests: list[RepairRequest] = []
    for item in items:
        start_time = as_utc(item["start_time"])
        if start_time is None:
            raise ServiceValidationError("start_time could not be parsed.")
        requests.append(
            RepairRequest(
                statistic_id=item["statistic_id"],
                start_time=start_time,
                offset=float(item["offset"]),
                unit=item.get("unit"),
                reason=item.get("reason", ""),
                fingerprint=item.get("fingerprint"),
            )
        )
    return requests


# ---------------------------------------------------------------------------
# Read-only services
# ---------------------------------------------------------------------------
async def async_handle_scan(call: ServiceCall) -> ServiceResponse:
    """Handle energy_guard.scan_statistics (never modifies data)."""
    hass = call.hass
    hubs = _hubs_or_fail(hass, call, SERVICE_SCAN_STATISTICS)
    end = as_utc(call.data.get("end_time")) or dt_util.utcnow()
    responses: list[dict[str, Any]] = []

    for hub in hubs:
        detection = hub.config.detection
        start = as_utc(call.data.get("start_time")) or (
            end - timedelta(hours=detection.lookback_hours)
        )
        statistic_ids: list[str] = list(call.data.get("statistic_ids") or [])
        statistic_ids.extend(call.data.get("entity_ids") or [])
        if not statistic_ids:
            statistic_ids = hub.statistic_ids()
        statistic_ids = list(dict.fromkeys(statistic_ids))
        if not statistic_ids:
            raise ServiceValidationError(
                "No statistic ids to scan. Either configure protected sensors first "
                "or pass statistic_ids/entity_ids to energy_guard.scan_statistics."
            )

        cost = hub.config.cost
        include_cost = call.data.get("include_cost_suggestions", True) and cost.enabled
        # A dynamic tariff (price_entity_id) is read once per scan; a fixed
        # price is used when the entity is missing or unusable.
        price = effective_price(hass, cost) if include_cost else None
        try:
            result = await async_scan(
                hass,
                start=start,
                end=end,
                statistic_ids=statistic_ids,
                detection=detection,
                cost_energy_statistic_id=cost.energy_statistic_id
                if include_cost
                else None,
                cost_statistic_id=cost.cost_statistic_id if include_cost else None,
                price=price,
                currency=cost.currency if include_cost else None,
            )
        except RecorderUnavailableError as err:
            raise ServiceValidationError(str(err)) from err

        hub.scan_candidates = [item.to_dict() for item in result.candidates]
        hub.last_scan = dt_util.utcnow()
        hub.last_scan_error = None
        hub.async_update_listeners()
        await async_sync_repair_issues(hass, hub)
        responses.append(result.to_dict())

    if len(responses) == 1:
        return responses[0]
    return {"config_entries": responses, "read_only": True}


async def async_handle_export_templates(
    call: ServiceCall,
) -> ServiceResponse:
    """Handle energy_guard.export_templates."""
    hass = call.hass
    hubs = _hubs_or_fail(hass, call, SERVICE_EXPORT_TEMPLATES)
    # The exported template file is a single file, so the first matching entry
    # (or the one named by config_entry_id) is used. Other entries can export
    # their own copy by passing config_entry_id explicitly.
    hub = hubs[0]
    content = render_templates(
        hub.config.protected, hub.config.derived, hub.config.utility_meters
    )
    result: dict[str, Any] = {
        "yaml": content,
        "sensor_count": len(
            [
                item
                for item in (*hub.config.protected, *hub.config.derived)
                if item.enabled
            ]
        ),
        "note": (
            "This YAML is a copy of your Energy Guard definitions. Energy Guard never "
            "reads or edits your configuration files."
        ),
    }
    if call.data.get("write_file"):
        path = hub.base_dir / TEMPLATES_FILE
        result["written_to"] = await async_write_templates(hass, path, content)
    return result


async def async_handle_export_report(
    call: ServiceCall,
) -> ServiceResponse:
    """Handle energy_guard.export_repair_report."""
    hass = call.hass
    hubs = _hubs_or_fail(hass, call, SERVICE_REPORT)
    hours = call.data.get("hours", 168)
    name = call.data.get("name") or "energy_guard_report"
    outputs: list[dict[str, Any]] = []

    for hub in hubs:
        since = dt_util.utcnow() - timedelta(hours=hours)
        report = build_report(
            hub_events=[event for event in hub.events if event.timestamp >= since],
            candidates=list(hub.scan_candidates),
            repairs=[
                record
                for record in hub.repairs
                if (record.get("timestamp") or "") >= since.isoformat()
            ],
            calibrations=[
                record
                for record in hub.calibrations
                if (record.get("timestamp") or "") >= since.isoformat()
            ],
            extra={
                "config_entry_id": hub.entry.entry_id,
                "protected_sensors": len(hub.config.protected),
                "derived_sensors": len(hub.config.derived),
                "detection_rules": hub.config.detection.to_dict(),
                "cost_repair": hub.config.cost.to_dict(),
                "backup_directory": str(hub.backup_dir),
                "last_scan": hub.last_scan.isoformat() if hub.last_scan else None,
                "recorder_available": True,
            },
        )
        location = await async_export_report(
            hass, directory=hub.report_dir, report=report, name=name
        )
        hub.log_event(
            build_anomaly_event(
                "report_exported",
                SEVERITY_INFO,
                f"Repair report written to {location['json_file']}",
                details=location,
            )
        )
        outputs.append({"config_entry_id": hub.entry.entry_id, **location})

    if len(outputs) == 1:
        return outputs[0]
    return {"config_entries": outputs}


# ---------------------------------------------------------------------------
# Confirmation-gated services
# ---------------------------------------------------------------------------
async def async_handle_repair(call: ServiceCall) -> ServiceResponse:
    """Handle energy_guard.repair_statistics."""
    from .statistics import verified_all

    hass = call.hass

    hubs = _hubs_or_fail(hass, call, SERVICE_REPAIR_STATISTICS)
    requests = _repair_requests(call.data["repairs"])
    cost_requests = _repair_requests(call.data.get("cost_repairs") or [])
    confirm: bool = call.data["confirm"]
    confirm_cost: bool = call.data["confirm_cost"]
    dry_run: bool = call.data["dry_run"]

    reports: list[dict[str, Any]] = []
    for hub in hubs:
        if cost_requests and not confirm_cost:
            raise ServiceValidationError(
                "Monetary repairs need their own confirmation: repeat the service "
                "call with confirm_cost: true (and confirm: true) after reviewing the "
                "preview returned by energy_guard.scan_statistics. Nothing was "
                f"changed. Preview: {_cost_preview(hass, hub, cost_requests)}"
            )
        target_hub = hub
        report = await async_repair_statistics(
            hass,
            requests=requests,
            confirm=confirm,
            dry_run=dry_run,
            create_backup=call.data["create_backup"],
            verify=call.data["verify"],
            backup_dir=target_hub.backup_dir,
            keep_backups=target_hub.config.backups.keep_backups,
        )

        if cost_requests and confirm_cost and confirm and not dry_run:
            cost_report = await async_repair_statistics(
                hass,
                requests=cost_requests,
                confirm=confirm,
                dry_run=dry_run,
                create_backup=call.data["create_backup"],
                verify=call.data["verify"],
                backup_dir=target_hub.backup_dir,
                keep_backups=target_hub.config.backups.keep_backups,
            )
            for cost_outcome in cost_report.applied:
                report.applied.append(cost_outcome)
            for cost_outcome in cost_report.preview:
                report.preview.append(cost_outcome)
            report.backups.extend(cost_report.backups)
            report.rollback.extend(cost_report.rollback)
            report.skipped.extend(cost_report.skipped)
            if cost_report.applied:
                report.status = "applied"
                report.message = (
                    f"Applied {len(report.applied)} repair(s), including "
                    f"{len(cost_report.applied)} monetary correction(s)."
                )
        elif cost_requests:
            report.cost_repairs_required_confirmation = [
                item.to_dict() for item in cost_requests
            ]

        for outcome in report.applied:
            target_hub.record_repair(
                {
                    "timestamp": dt_util.utcnow().isoformat(),
                    "statistic_id": outcome.statistic_id,
                    "start_time": outcome.start_time.isoformat(),
                    "offset": outcome.offset,
                    "unit": outcome.unit,
                    "before_value": outcome.before_value,
                    "after_value": outcome.after_value,
                    "expected_value": outcome.expected_value,
                    "verified": outcome.verified,
                    "backup_file": outcome.backup_file,
                }
            )
            target_hub.log_event(
                build_anomaly_event(
                    EVENT_REPAIRED,
                    SEVERITY_INFO,
                    (
                        f"Repaired {outcome.statistic_id}: applied {outcome.offset} "
                        f"{outcome.unit} from {outcome.start_time.isoformat()} "
                        f"(verified: {outcome.verified})"
                    ),
                    source_entity=outcome.statistic_id,
                    protected_entity=outcome.statistic_id,
                    invalid_value=outcome.offset,
                    previous_value=outcome.before_value,
                    restored_value=outcome.after_value,
                    unit=outcome.unit,
                    details={
                        **outcome.to_dict(),
                        "recommended_action": RECOMMENDED_ACTIONS[EVENT_REPAIRED],
                    },
                )
            )

        await async_sync_repair_issues(hass, target_hub)

        payload = report.to_dict()
        if not verified_all(report) and report.applied:
            payload["verification_warning"] = (
                "At least one repair could not be verified against the recorder. Read "
                "the statistic again in a few minutes; the 'rollback' entries contain "
                "the inverse adjustment and the backup file names."
            )
        reports.append(payload)

    if len(reports) == 1:
        return reports[0]
    return {"config_entries": reports}


async def async_handle_clear(call: ServiceCall) -> ServiceResponse:
    """Handle energy_guard.clear_statistics."""
    hass = call.hass
    hubs = _hubs_or_fail(hass, call, SERVICE_CLEAR_STATISTICS)
    statistic_ids = list(call.data["statistic_ids"])
    if not statistic_ids:
        raise ServiceValidationError("statistic_ids must not be empty.")

    results: list[dict[str, Any]] = []
    first = True
    for hub in hubs:
        result = await async_clear_statistics(
            hass,
            statistic_ids=statistic_ids,
            confirm=call.data["confirm"],
            create_backup=call.data["create_backup"],
            backup_dir=hub.backup_dir,
            keep_backups=hub.config.backups.keep_backups,
        )
        await async_sync_repair_issues(hass, hub)
        if result.get("status") == "cleared" and first:
            hub.log_event(
                build_anomaly_event(
                    EVENT_CLEARED,
                    SEVERITY_WARNING,
                    f"Cleared statistics for: {', '.join(result['cleared'])}",
                    details={
                        "statistic_ids": result["cleared"],
                        "recommended_action": RECOMMENDED_ACTIONS[EVENT_CLEARED],
                    },
                )
            )
        first = False
        results.append(result)

    if len(results) == 1:
        return results[0]
    return {"config_entries": results}


async def async_handle_calibrate(
    call: ServiceCall,
) -> ServiceResponse:
    """Handle energy_guard.calibrate_utility_meter."""
    hass = call.hass
    hubs = _hubs_or_fail(hass, call, SERVICE_CALIBRATE_UTILITY_METER)
    entity_id: str = call.data["entity_id"]
    value = call.data.get("value")
    source_entity_id = call.data.get("source_entity_id")
    baseline_value = call.data.get("baseline_value")
    cycle_start = call.data.get("cycle_start")
    cycle = call.data.get("cycle")

    if value is None and source_entity_id is None:
        raise ServiceValidationError(
            "Provide 'value' (the exact meter value) or 'source_entity_id' (a "
            "protected cumulative sensor to calculate it from)."
        )

    # A configured utility meter may supply the source/baseline defaults.
    for hub in hubs:
        if source_entity_id:
            break
        for meter in hub.config.utility_meters:
            if meter.utility_meter_entity_id == entity_id and meter.enabled:
                source_entity_id = meter.source_entity_id
                baseline_value = (
                    baseline_value
                    if baseline_value is not None
                    else meter.baseline_value
                )
                cycle = cycle or meter.cycle
                if meter.baseline_at:
                    cycle_start = cycle_start or as_utc(meter.baseline_at)
                break

    result = await async_calibrate_utility_meter(
        hass,
        entity_id=entity_id,
        value=value,
        source_entity_id=source_entity_id,
        baseline_value=baseline_value,
        cycle_start=cycle_start,
        cycle=cycle,
        confirm=call.data["confirm"],
        dry_run=call.data["dry_run"],
    )

    if result.get("applied"):
        hub = hubs[0]
        hub.record_calibration(
            {
                "timestamp": dt_util.utcnow().isoformat(),
                "entity_id": entity_id,
                "current_value": result.get("current_value"),
                "target_value": result.get("target_value"),
                "new_value": result.get("new_value"),
                "unit": result.get("unit"),
                "verified": result.get("verified"),
                "source_entity_id": source_entity_id,
                "baseline_value": baseline_value,
            }
        )
        hub.log_event(
            build_anomaly_event(
                EVENT_CALIBRATED,
                SEVERITY_INFO,
                f"Calibrated {entity_id} to {result.get('target_value')} "
                f"{result.get('unit')} (was {result.get('current_value')})",
                source_entity=source_entity_id,
                protected_entity=entity_id,
                previous_value=result.get("current_value"),
                restored_value=result.get("new_value"),
                unit=result.get("unit"),
                details={
                    **result,
                    "recommended_action": RECOMMENDED_ACTIONS[EVENT_CALIBRATED],
                },
            )
        )
        await async_sync_repair_issues(hass, hub)
    return result


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def async_register_services(hass: HomeAssistant) -> None:
    """Register all Energy Guard services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_SCAN_STATISTICS):
        return
    api = hass.services
    api.async_register(
        DOMAIN,
        SERVICE_SCAN_STATISTICS,
        async_handle_scan,
        schema=SCAN_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    api.async_register(
        DOMAIN,
        SERVICE_REPORT,
        async_handle_export_report,
        schema=REPORT_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    api.async_register(
        DOMAIN,
        SERVICE_EXPORT_TEMPLATES,
        async_handle_export_templates,
        schema=EXPORT_TEMPLATES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    # Modifying services are admin only and return a preview unless confirmed.
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_REPAIR_STATISTICS,
        async_handle_repair,
        schema=REPAIR_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_CLEAR_STATISTICS,
        async_handle_clear,
        schema=CLEAR_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_CALIBRATE_UTILITY_METER,
        async_handle_calibrate,
        schema=CALIBRATE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove all Energy Guard services."""
    for service in SERVICE_NAMES:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
