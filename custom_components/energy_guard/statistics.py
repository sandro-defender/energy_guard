"""Repairing and clearing recorder statistics.

This module owns every *write* operation on statistics:

* :func:`async_repair_statistics` previews or applies explicitly supplied sum
  offsets (never "the thing Energy Guard thinks is wrong"),
* :func:`async_clear_statistics` clears exactly the statistics the user listed.

Both follow the same safety order: validate -> preview values -> JSON backup ->
write through the official recorder API -> wait for the recorder queue -> read
the statistic back -> report what actually happened (including the exact
rollback offset).  Detection lives in :mod:`.detection`, backups in
:mod:`.backups`, recorder access in :mod:`.recorder_io`, reporting in
:mod:`.export`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from .backups import async_backup_statistics
from .detection import fingerprint
from .models import RepairOutcome, RepairRequest
from .recorder_io import (
    VERIFY_ATTEMPTS,
    VERIFY_DELAY,
    anchor_sums,
    as_utc,
    async_statistic_metadata,
    async_statistics_rows,
    async_wait_for_recorder,
    iso_timestamp,
    last_sum,
    recorder_instance,
    recorder_is_available,
    stored_unit,
    verify_adjustment_applied,
)
from .units import convert, units_are_compatible

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Recorder calls
# ---------------------------------------------------------------------------
async def _async_call_recorder_write(
    hass: HomeAssistant, method: str, payload: tuple[Any, ...]
) -> None:
    """Call a mutating recorder API, with a WebSocket handler fallback."""
    recorder = recorder_instance(hass)
    target = getattr(recorder, method, None)
    if target is not None:
        target(*payload)
        return
    # Fallback: the recorder WebSocket handler is the official public entry point.
    connection = _CapturingConnection()
    if method == "async_adjust_statistics":
        from homeassistant.components.recorder.websocket_api import (
            ws_adjust_sum_statistics,
        )

        statistic_id, start_time, adjustment, unit = payload
        await ws_adjust_sum_statistics(
            hass,
            connection,
            {
                "id": 1,
                "type": "recorder/adjust_sum_statistics",
                "statistic_id": statistic_id,
                "start_time": start_time.isoformat(),
                "adjustment": adjustment,
                "adjustment_unit_of_measurement": unit,
            },
        )
    elif method == "async_clear_statistics":
        from homeassistant.components.recorder.websocket_api import (
            ws_clear_statistics,
        )

        (statistic_ids,) = payload
        await ws_clear_statistics(
            hass,
            connection,
            {
                "id": 1,
                "type": "recorder/clear_statistics",
                "statistic_ids": statistic_ids,
            },
        )
    else:  # pragma: no cover - defensive
        raise HomeAssistantError(
            f"Energy Guard cannot use recorder API '{method}' in this Home Assistant "
            "version. Please update Home Assistant and try again."
        )
    if connection.error:
        raise ServiceValidationError(
            f"Recorder rejected the request: {connection.error[1]} "
            f"({connection.error[0]})"
        )


class _CapturingConnection:
    """Minimal ActiveConnection stand-in for the recorder WebSocket handlers."""

    def __init__(self) -> None:
        """Initialise the capturing connection."""
        self.result: Any = None
        self.error: tuple[str, str] | None = None

    def send_result(self, msg_id: int, result: Any = None) -> None:
        """Capture a successful result."""
        self.result = result

    def send_error(self, msg_id: int, code: str, message: str) -> None:
        """Capture an error."""
        self.error = (code, message)

    def send_message(self, message: Any) -> None:  # pragma: no cover - fallback
        """Ignore unrelated messages."""
        return None

    async def send_binary(self, payload: Any) -> None:  # pragma: no cover
        """Ignore binary messages."""
        return None


@dataclass(slots=True)
class RepairReport:
    """The complete result of a repair (or repair preview) request."""

    status: str
    confirmed: bool
    dry_run: bool
    applied: list[RepairOutcome] = field(default_factory=list)
    preview: list[RepairOutcome] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    backups: list[dict[str, Any]] = field(default_factory=list)
    rollback: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    cost_repairs_required_confirmation: list[dict[str, Any]] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serialisable representation."""
        return {
            "status": self.status,
            "confirmed": self.confirmed,
            "dry_run": self.dry_run,
            "applied_count": len(self.applied),
            "applied": [item.to_dict() for item in self.applied],
            "preview": [item.to_dict() for item in self.preview],
            "skipped": self.skipped,
            "backups": self.backups,
            "rollback": self.rollback,
            "cost_repairs_awaiting_confirmation": self.cost_repairs_required_confirmation,
            "message": self.message,
        }


async def async_repair_statistics(
    hass: HomeAssistant,
    *,
    requests: list[RepairRequest],
    confirm: bool,
    dry_run: bool,
    create_backup: bool,
    verify: bool,
    backup_dir: Path,
    keep_backups: int = 25,
    unit_hint: str | None = None,
) -> RepairReport:
    """Preview or apply a list of explicitly supplied sum offsets.

    Nothing is written unless ``confirm`` is true and ``dry_run`` is false.
    """
    report = RepairReport(status="preview", confirmed=confirm, dry_run=dry_run)
    if not requests:
        report.message = "No repair items were supplied, nothing to do."
        return report

    metadata = await async_statistic_metadata(
        hass, [item.statistic_id for item in requests]
    )
    for request in requests:
        statistic_id = request.statistic_id
        start_time = as_utc(request.start_time)
        meta = metadata.get(statistic_id)
        if meta is None:
            report.skipped.append(
                {
                    "statistic_id": statistic_id,
                    "reason": "unknown_statistic_id",
                    "message": (
                        f"'{statistic_id}' has no statistics in the recorder. Use the "
                        "statistic id (sensor.xyz) of an entity that has long term "
                        "statistics."
                    ),
                }
            )
            continue
        if not meta.get("has_sum", True):
            report.skipped.append(
                {
                    "statistic_id": statistic_id,
                    "reason": "statistic_has_no_sum",
                    "message": f"'{statistic_id}' is not a cumulative (sum) statistic.",
                }
            )
            continue

        target_unit = stored_unit(meta) or unit_hint or ""
        request_unit = request.unit or target_unit
        if (
            not units_are_compatible(request_unit, target_unit)
            and request_unit != target_unit
        ):
            report.skipped.append(
                {
                    "statistic_id": statistic_id,
                    "reason": "incompatible_unit",
                    "message": (
                        f"Offset unit '{request_unit}' cannot be applied to "
                        f"'{statistic_id}' which is stored in '{target_unit}'."
                    ),
                }
            )
            continue

        adjustment = float(request.offset)
        if request_unit != target_unit:
            adjustment = convert(adjustment, request_unit, target_unit)

        if request.fingerprint and request.fingerprint != fingerprint(
            statistic_id, start_time, float(request.offset), request.unit or target_unit
        ):
            report.skipped.append(
                {
                    "statistic_id": statistic_id,
                    "reason": "fingerprint_mismatch",
                    "message": (
                        "The supplied fingerprint does not match this repair item. "
                        "Run energy_guard.scan_statistics again and confirm the "
                        "current candidate."
                    ),
                }
            )
            continue

        # Preview values -------------------------------------------------
        window_end = dt_util.utcnow()
        rows = (
            await async_statistics_rows(hass, [statistic_id], start_time, window_end)
        ).get(statistic_id, [])
        total_before = last_sum(rows)
        anchors_before = anchor_sums(rows, start_time)

        outcome = RepairOutcome(
            statistic_id=statistic_id,
            start_time=start_time,
            offset=float(request.offset),
            unit=target_unit,
            applied=False,
            before_value=total_before,
            expected_value=(
                round(total_before + adjustment, 6)
                if total_before is not None
                else None
            ),
            rollback_adjustment=round(-adjustment, 6),
        )

        rollback = {
            "statistic_id": statistic_id,
            "start_time": start_time.isoformat(),
            "offset": round(-adjustment, 6),
            "unit": target_unit,
            "backup_file": None,
            "message": (
                "To undo this repair, call energy_guard.repair_statistics with this "
                "inverse offset (and confirm: true). The backup file contains the "
                "original rows."
            ),
        }

        if not confirm or dry_run:
            report.preview.append(outcome)
            report.rollback.append(rollback)
            continue

        # Backup ---------------------------------------------------------
        if create_backup:
            try:
                backup = await async_backup_statistics(
                    hass,
                    directory=backup_dir,
                    statistic_id=statistic_id,
                    unit=target_unit,
                    start=start_time,
                    end=window_end,
                    keep=keep_backups,
                    reason=(
                        f"offset {request.offset} {request_unit} at "
                        f"{start_time.isoformat()} ({request.reason or 'manual repair'})"
                    ),
                )
            except OSError as err:
                # Rule: never change a statistic that could not be backed up.
                _LOGGER.error(
                    "Energy Guard could not back up %s (%s); the repair was skipped",
                    statistic_id,
                    err,
                )
                report.skipped.append(
                    {
                        "statistic_id": statistic_id,
                        "reason": "backup_failed",
                        "message": (
                            f"Could not write the backup file ({err}). Nothing was "
                            "changed. Free disk space or fix the permissions of the "
                            "backup directory and try again."
                        ),
                    }
                )
                continue
            outcome.backup_file = backup["file"]
            report.backups.append(backup)

        # Apply ----------------------------------------------------------
        try:
            await _async_call_recorder_write(
                hass,
                "async_adjust_statistics",
                (statistic_id, start_time, adjustment, target_unit),
            )
        except (HomeAssistantError, ServiceValidationError) as err:
            outcome.error = str(err)
            report.skipped.append(
                {
                    "statistic_id": statistic_id,
                    "reason": "recorder_error",
                    "message": str(err),
                    "backup_file": outcome.backup_file,
                }
            )
            continue

        outcome.applied = True

        if verify:
            after_at_start, after_last, verified = await verify_adjustment_applied(
                hass,
                statistic_id=statistic_id,
                start_time=start_time,
                adjustment=adjustment,
                anchors_before=anchors_before,
            )
            outcome.after_value = after_last if after_last is not None else total_before
            if outcome.expected_value is None and after_at_start is not None:
                outcome.expected_value = round(after_at_start, 6)
            outcome.verified = verified

        report.applied.append(outcome)
        rollback["backup_file"] = outcome.backup_file
        report.rollback.append(rollback)

    if not confirm or dry_run:
        report.status = "preview"
        report.message = (
            f"Preview only: {len(report.preview)} repair(s) prepared, no data was "
            "modified. Re-run with confirm: true and dry_run: false to apply."
        )
    elif report.applied and report.skipped:
        report.status = "partial"
        report.message = (
            f"Applied {len(report.applied)} repair(s); {len(report.skipped)} item(s) "
            "were skipped. Check the 'skipped' list."
        )
    elif report.applied:
        failed_verification = [
            item.statistic_id for item in report.applied if item.verified is False
        ]
        report.status = "applied"
        report.message = f"Applied {len(report.applied)} repair(s)." + (
            " Verification failed for: " + ", ".join(failed_verification)
            if failed_verification
            else " All repairs were verified against the recorder."
        )
    else:
        report.status = "failed"
        report.message = "No repair was applied. Check the 'skipped' list for details."

    if report.applied and not verified_all(report):
        report.message += (
            " Some statistics could not be verified yet; read the affected statistic "
            "again after a few minutes, and use the reported 'rollback' adjustment if "
            "the value is still wrong."
        )
    return report


def verified_all(report: RepairReport) -> bool:
    """Return True when every applied repair was verified."""
    return all(item.verified is not False for item in report.applied)


async def async_clear_statistics(
    hass: HomeAssistant,
    *,
    statistic_ids: list[str],
    confirm: bool,
    create_backup: bool,
    backup_dir: Path,
    keep_backups: int = 25,
) -> dict[str, Any]:
    """Clear statistics of exactly the supplied statistic ids."""
    if not statistic_ids:
        raise ServiceValidationError(
            "energy_guard.clear_statistics needs at least one statistic_id."
        )

    metadata = await async_statistic_metadata(hass, statistic_ids)
    preview: list[dict[str, Any]] = []
    now = dt_util.utcnow()
    for statistic_id in statistic_ids:
        meta = metadata.get(statistic_id)
        if meta is None:
            preview.append(
                {
                    "statistic_id": statistic_id,
                    "found": False,
                    "rows": 0,
                    "message": "No statistics found; nothing would be cleared.",
                }
            )
            continue
        rows = (
            await async_statistics_rows(
                hass, [statistic_id], now - timedelta(days=3650), now
            )
        ).get(statistic_id, [])
        preview.append(
            {
                "statistic_id": statistic_id,
                "found": True,
                "unit": stored_unit(meta),
                "source": meta.get("source"),
                "rows": len(rows),
                "first_row": iso_timestamp(rows[0].get("start")) if rows else None,
                "last_row": iso_timestamp(rows[-1].get("start")) if rows else None,
            }
        )

    result: dict[str, Any] = {
        "confirm_required": True,
        "confirmed": confirm,
        "statistic_ids": statistic_ids,
        "preview": preview,
        "backups": [],
        "cleared": [],
        "message": "",
    }

    if not confirm:
        result["status"] = "confirmation_required"
        result["message"] = (
            "Nothing was cleared. Energy Guard never clears statistics without "
            "confirm: true. Review the preview and repeat the service call with "
            "confirm: true if the affected statistics are correct."
        )
        return result

    if create_backup and recorder_is_available(hass):
        for statistic_id in statistic_ids:
            if statistic_id not in metadata:
                continue
            try:
                backup = await async_backup_statistics(
                    hass,
                    directory=backup_dir,
                    statistic_id=statistic_id,
                    unit=stored_unit(metadata[statistic_id]) or "",
                    start=now - timedelta(days=3650),
                    end=now,
                    keep=keep_backups,
                    reason="statistics cleared",
                )
            except OSError as err:
                # Clearing without a backup is never acceptable.
                result["status"] = "failed"
                result["message"] = (
                    f"Could not back up {statistic_id} ({err}), so nothing was "
                    "cleared. Free disk space or fix the permissions of the backup "
                    "directory and try again, or pass create_backup: false if you "
                    "really do not want a backup."
                )
                return result
            result["backups"].append(backup)

    try:
        await _async_call_recorder_write(
            hass, "async_clear_statistics", (list(statistic_ids),)
        )
    except (HomeAssistantError, ServiceValidationError) as err:
        result["status"] = "failed"
        result["message"] = f"Recorder refused to clear the statistics: {err}"
        return result

    # The recorder applies the clear from its own queue, so give it a few
    # bounded attempts before reporting anything as still present.
    remaining: dict[str, dict[str, Any]] = {}
    for attempt in range(VERIFY_ATTEMPTS):
        await async_wait_for_recorder(hass)
        remaining = await async_statistic_metadata(hass, statistic_ids)
        pending = [item for item in statistic_ids if item in remaining]
        if not pending or attempt + 1 >= VERIFY_ATTEMPTS:
            break
        await asyncio.sleep(VERIFY_DELAY)
    result["cleared"] = [
        statistic_id for statistic_id in statistic_ids if statistic_id not in remaining
    ]
    failed = [
        statistic_id for statistic_id in statistic_ids if statistic_id in remaining
    ]
    result["status"] = "cleared" if not failed else "partial"
    result["message"] = (
        f"Cleared {len(result['cleared'])} statistic(s)."
        + (f" Still present: {', '.join(failed)}" if failed else "")
        + " Statistics will be rebuilt by the recorder for entities that are still "
        "reporting data."
    )
    return result
