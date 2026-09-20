"""Read-only detection of corrupted cumulative statistics.

The statistics scanner compares what the recorder *believes* (the ``sum`` of a
statistic, the value the Energy Dashboard uses) with what the sensor *reported*
(``state``) and turns disagreements into :class:`ScanCandidate` objects.

Nothing in this module writes anything: every function is pure or read-only, so
``energy_guard.scan_statistics`` can never change data.
"""

from __future__ import annotations

import hashlib
import logging
import statistics as py_statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any, Final

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_SCAN_SCOPE,
    MAX_DISCOVERED_STATISTICS,
    SCAN_SCOPE_ALL,
    SCAN_SCOPE_ENERGY,
    SEVERITY_WARNING,
)
from .costs import build_cost_suggestions
from .models import DetectionRules, ScanCandidate
from .recorder_io import (
    RecorderUnavailableError,
    as_utc,
    async_all_statistic_metadata,
    async_statistic_metadata,
    async_statistics_rows,
    iso_timestamp,
    recorder_is_available,
    stored_unit,
)
from .units import is_energy_unit

_LOGGER = logging.getLogger(__name__)

#: A single scan never looks back further than one year.
MAX_SCAN_HOURS: Final = 24 * 366


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
def fingerprint(
    statistic_id: str, start_time: datetime, offset: float, unit: str
) -> str:
    """Return a short, stable fingerprint for a suggested offset.

    The fingerprint ties a previewed offset to the exact repair call.  It is
    advisory: an explicit ``offset`` + ``start_time`` pair is enough to repair,
    but passing the fingerprint back guarantees the user confirmed *this*
    candidate.
    """
    raw = f"{statistic_id}|{as_utc(start_time).timestamp():.0f}|{offset:.6f}|{unit}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass(slots=True)
class ScanResult:
    """Result of a read-only statistics scan."""

    start: datetime
    end: datetime
    statistic_ids: list[str]
    candidates: list[ScanCandidate] = field(default_factory=list)
    cost_suggestions: list[dict[str, Any]] = field(default_factory=list)
    statistics: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    recorder_available: bool = True
    scope: str = DEFAULT_SCAN_SCOPE

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serialisable representation."""
        return {
            "status": "ok" if self.recorder_available else "recorder_unavailable",
            "read_only": True,
            "start_time": self.start.isoformat(),
            "end_time": self.end.isoformat(),
            "statistic_ids": self.statistic_ids,
            "statistic_count": len(self.statistic_ids),
            "scope": self.scope,
            "candidate_count": len(self.candidates),
            "candidates": [item.to_dict() for item in self.candidates],
            "cost_suggestions": self.cost_suggestions,
            "statistics": self.statistics,
            "warnings": self.warnings,
        }


def median_positive(values: list[float]) -> float | None:
    """Return the median of the positive values, or None."""
    positive = [value for value in values if value > 0]
    if len(positive) < 3:
        return None
    return float(py_statistics.median(positive))


def detect_offsets(
    rows: list[dict[str, Any]],
    *,
    statistic_id: str,
    unit: str,
    detection: DetectionRules,
    now: datetime,
    min_previous_state: float = 1.0,
) -> list[ScanCandidate]:
    """Detect suspicious sum offsets in hourly statistics rows.

    Three signatures are reported:

    ``zero_then_restore``
        The recorded state was (near) zero and then returned to a high value
        while the sum grew by roughly that high value.  This is the exact
        fingerprint of a reconnect that reported ``0`` for one period.
    ``sum_state_mismatch``
        The sum grew far faster than the entity's own recorded state.
    ``large_jump``
        The sum grew by an implausible amount compared to the history.
    """
    candidates: list[ScanCandidate] = []
    if len(rows) < 2:
        return candidates

    deltas: list[float] = []
    for previous, current in pairwise(rows):
        if previous.get("sum") is None or current.get("sum") is None:
            continue
        deltas.append(float(current["sum"]) - float(previous["sum"]))

    baseline = median_positive(deltas) or 0.0
    threshold = max(
        detection.statistics_jump_threshold, detection.statistics_jump_ratio * baseline
    )
    epsilon = 1e-9

    for previous, current in pairwise(rows):
        previous_sum = previous.get("sum")
        current_sum = current.get("sum")
        if previous_sum is None or current_sum is None:
            continue
        sum_delta = float(current_sum) - float(previous_sum)
        previous_state = previous.get("state")
        current_state = current.get("state")
        state_delta = (
            float(current_state) - float(previous_state)
            if previous_state is not None and current_state is not None
            else None
        )
        start_time = as_utc(current["start"])
        evidence: list[str] = []
        expected = baseline
        severity = SEVERITY_WARNING

        zero_then_restore = (
            previous_state is not None
            and current_state is not None
            and float(previous_state) <= epsilon
            and float(current_state) >= max(min_previous_state, threshold)
            and sum_delta >= float(current_state) * 0.9
        )
        if zero_then_restore:
            evidence.append("zero_then_restore")
            evidence.append("source_was_zero_in_previous_period")
            expected = baseline
            severity = "error"

        elif sum_delta > threshold:
            evidence.append("large_jump")
            if state_delta is not None and state_delta <= epsilon:
                evidence.append("state_flat_while_sum_grew")
                severity = "error"
            elif state_delta is not None and sum_delta > max(
                threshold, detection.sum_state_ratio * state_delta
            ):
                evidence.append("sum_state_mismatch")
                severity = "error"
            expected = max(baseline, max(0.0, state_delta or 0.0))
        else:
            continue

        if (
            current.get("last_reset") != previous.get("last_reset")
            and current.get("last_reset") is not None
        ):
            evidence.append("last_reset_changed")
            severity = SEVERITY_WARNING

        false_energy = max(0.0, sum_delta - expected)
        offset = -round(false_energy, 6)
        if abs(offset) <= epsilon:
            continue
        candidates.append(
            ScanCandidate(
                statistic_id=statistic_id,
                start_time=start_time,
                detected_at=now,
                unit=unit,
                offset=offset,
                observed_delta=round(sum_delta, 6),
                expected_delta=round(expected, 6),
                estimated_false_energy=round(false_energy, 6),
                evidence=evidence,
                severity=severity,
                fingerprint=fingerprint(statistic_id, start_time, offset, unit),
            )
        )
    return candidates


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------
async def async_discover_statistic_ids(
    hass: HomeAssistant,
    *,
    scope: str,
    linked_ids: Iterable[str] = (),
    max_statistics: int = MAX_DISCOVERED_STATISTICS,
) -> tuple[list[str], list[str]]:
    """Return the statistic ids a scan with ``scope`` should cover.

    ``linked`` keeps the historic behaviour (only the statistics Energy Guard
    is linked to), ``energy`` adds every cumulative statistic with an energy
    unit and ``all`` every cumulative statistic in the recorder.  Statistics
    Energy Guard is already linked to come first, so a truncated "scan
    everything" run still covers the sensors the user configured.

    Returns the ids and the warnings that belong into the scan response.
    """
    linked = list(dict.fromkeys(str(item) for item in linked_ids if item))
    if scope not in (SCAN_SCOPE_ENERGY, SCAN_SCOPE_ALL):
        return linked, []

    try:
        metadata = await async_all_statistic_metadata(hass)
    except RecorderUnavailableError:
        return linked, [
            "The recorder is not available, so the available statistics could "
            "not be listed. Only the Energy Guard statistics were scanned."
        ]

    ids: list[str] = []
    for item in metadata:
        statistic_id = item.get("statistic_id")
        if not statistic_id or statistic_id in linked:
            continue
        if scope == SCAN_SCOPE_ENERGY and not (
            is_energy_unit(stored_unit(item)) or item.get("unit_class") == "energy"
        ):
            continue
        ids.append(str(statistic_id))

    ids.sort()
    warnings: list[str] = []
    if len(ids) > max_statistics:
        warnings.append(
            f"{len(ids)} statistics are available; only the first {max_statistics} "
            "were scanned. Narrow the lookback or pass statistic_ids to scan the "
            "rest."
        )
        ids = ids[:max_statistics]
    if not ids and not linked:
        warnings.append(
            "No statistics were found for this scope. Check that the sensors have "
            "long term statistics (they need a state class and the recorder)."
        )
    return linked + ids, warnings


async def async_scan(
    hass: HomeAssistant,
    *,
    start: datetime,
    end: datetime,
    statistic_ids: list[str],
    detection: DetectionRules,
    cost_energy_statistic_id: str | None = None,
    cost_statistic_id: str | None = None,
    price: float | None = None,
    currency: str | None = None,
    min_previous_state: float = 1.0,
) -> ScanResult:
    """Scan statistics for suspicious offsets without modifying anything."""
    start = as_utc(start) or dt_util.utcnow()
    end = as_utc(end) or dt_util.utcnow()
    now = dt_util.utcnow()
    result = ScanResult(start=start, end=end, statistic_ids=list(statistic_ids))

    if (end - start) > timedelta(hours=MAX_SCAN_HOURS):
        result.warnings.append(
            "The requested range is longer than one year and was truncated."
        )
        start = end - timedelta(hours=MAX_SCAN_HOURS)

    if not recorder_is_available(hass):
        result.recorder_available = False
        result.warnings.append(
            "The recorder is not available, so no statistics could be read."
        )
        return result

    metadata = await async_statistic_metadata(hass, statistic_ids)
    units = {
        statistic_id: unit
        for statistic_id, item in metadata.items()
        if (unit := stored_unit(item)) is not None
    }
    known_ids = [item for item in statistic_ids if item in metadata]
    for statistic_id in statistic_ids:
        if statistic_id not in metadata:
            result.warnings.append(
                f"{statistic_id} has no statistics in the recorder; skipped."
            )

    rows_by_id = await async_statistics_rows(hass, known_ids, start, end, units or None)

    for statistic_id in known_ids:
        rows = rows_by_id.get(statistic_id) or []
        unit = units.get(statistic_id) or ""
        if not rows:
            continue
        sums = [row["sum"] for row in rows if row.get("sum") is not None]
        result.statistics[statistic_id] = {
            "unit": unit,
            "rows": len(rows),
            "first_row": iso_timestamp(rows[0].get("start")),
            "last_row": iso_timestamp(rows[-1].get("start")),
            "total_increase": round(float(sums[-1]) - float(sums[0]), 3)
            if len(sums) > 1
            else 0.0,
        }
        result.candidates.extend(
            detect_offsets(
                rows,
                statistic_id=statistic_id,
                unit=unit,
                detection=detection,
                now=now,
                min_previous_state=min_previous_state,
            )
        )

    result.candidates.sort(key=lambda item: (item.start_time, item.statistic_id))

    # Mirror the energy offsets into the linked cost statistic.  These are
    # suggestions only: applying money always needs its own confirmation.
    if cost_statistic_id and price:
        result.cost_suggestions = build_cost_suggestions(
            result.candidates,
            cost_statistic_id=cost_statistic_id,
            price=price,
            currency=currency,
            energy_statistic_id=cost_energy_statistic_id,
            fingerprint_fn=fingerprint,
        )
    return result
