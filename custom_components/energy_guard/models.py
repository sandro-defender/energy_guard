"""Data models used by Energy Guard.

Everything in this module is intentionally free of Home Assistant imports so it
can be unit tested without a running Home Assistant instance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .const import (
    DEFAULT_ACCEPT_RESET,
    DEFAULT_CONFIRM_SCANS,
    DEFAULT_DO_NOT_DECREASE,
    DEFAULT_GRACE_PERIOD,
    DEFAULT_LARGE_JUMP,
    DEFAULT_LARGE_JUMP_RATIO,
    DEFAULT_PRECISION,
    DEFAULT_RECOVERY_HOLD_SCANS,
    DEFAULT_REJECT_LARGE_JUMPS,
    DEFAULT_UNIT,
    DEFAULT_ZERO_MIN_PREVIOUS,
    MODE_SUM,
    SCAN_SCOPE_LINKED,
    SCAN_SCOPES,
)


def _serializable(value: Any) -> Any:
    """Convert datetimes to ISO strings so the value is JSON serialisable."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serializable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serializable(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Value normalisation
#
# The options flow stores whatever the Home Assistant selectors return, and a
# number selector returns a float even for an integer field (``precision``:
# ``3.0``).  The dataclasses declare ``int`` where an integer is required, so
# stored JSON has to be normalised on the way in: ``round(value, 3.0)`` used to
# raise ``TypeError`` and abort entity setup for a UI configured sensor.
# ---------------------------------------------------------------------------
def _as_int(value: Any, default: int) -> int:
    """Return ``value`` as an int, rounding floats and numeric strings."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value) if math.isfinite(value) else default
    if isinstance(value, str):
        try:
            return round(float(value.strip()))
        except ValueError:
            return default
    return default


def _as_float(value: Any, default: float | None) -> float | None:
    """Return ``value`` as a finite float, or ``default``."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else default
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return default
        return number if math.isfinite(number) else default
    return default


def _as_bool(value: Any, default: bool) -> bool:
    """Return ``value`` as a bool, understanding the usual string spellings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off", ""):
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_str(value: Any, default: str | None) -> str | None:
    """Return ``value`` as a stripped string (empty becomes ``default``)."""
    if value is None:
        return default
    text = value.strip() if isinstance(value, str) else str(value)
    return text or default


def _as_str_list(value: Any) -> list[str]:
    """Return ``value`` as a list of unique, non-empty strings."""
    if isinstance(value, str):
        items: list[Any] = [value]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        return []
    result: list[str] = []
    for item in items:
        text = _as_str(item, None)
        if text and text not in result:
            result.append(text)
    return result


def _coerce(
    values: dict[str, Any],
    *,
    ints: dict[str, int] | None = None,
    floats: dict[str, float] | None = None,
    optional_floats: tuple[str, ...] = (),
    bools: dict[str, bool] | None = None,
    strings: dict[str, str | None] | None = None,
    string_lists: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Coerce present values to the field types the dataclass declares.

    Missing keys are left alone so the dataclass defaults still apply.
    """
    for key, default in (ints or {}).items():
        if key in values:
            values[key] = _as_int(values[key], default)
    for key, default in (floats or {}).items():
        if key in values:
            values[key] = _as_float(values[key], default)
    for key in optional_floats:
        if key in values:
            values[key] = _as_float(values[key], None)
    for key, default in (bools or {}).items():
        if key in values:
            values[key] = _as_bool(values[key], default)
    for key, default in (strings or {}).items():
        if key in values:
            values[key] = _as_str(values[key], default)
    for key in string_lists:
        if key in values:
            values[key] = _as_str_list(values[key])
    return values


@dataclass(slots=True)
class SensorDefinition:
    """Definition of a protected (or derived) energy sensor."""

    id: str
    name: str
    enabled: bool = True
    source_entity_id: str | None = None
    source_entity_ids: list[str] = field(default_factory=list)
    source_unit: str | None = None
    mode: str = MODE_SUM
    total_entity_id: str | None = None
    part_entity_ids: list[str] = field(default_factory=list)
    scale: float = 1.0
    require_all_sources: bool = True
    unit_of_measurement: str = DEFAULT_UNIT
    offset: float = 0.0
    do_not_decrease: bool = DEFAULT_DO_NOT_DECREASE
    accept_real_reset: bool = DEFAULT_ACCEPT_RESET
    zero_min_previous: float = DEFAULT_ZERO_MIN_PREVIOUS
    confirm_scans: int = DEFAULT_CONFIRM_SCANS
    recovery_hold_scans: int = DEFAULT_RECOVERY_HOLD_SCANS
    grace_period: int = DEFAULT_GRACE_PERIOD
    large_jump: float = DEFAULT_LARGE_JUMP
    large_jump_ratio: float = DEFAULT_LARGE_JUMP_RATIO
    reject_large_jumps: bool = DEFAULT_REJECT_LARGE_JUMPS
    max_value: float | None = None
    precision: int = DEFAULT_PRECISION

    @property
    def is_derived(self) -> bool:
        """Return True when this definition describes a derived sensor."""
        return bool(self.source_entity_ids or self.part_entity_ids)

    @property
    def all_sources(self) -> list[str]:
        """Return every entity this definition depends on."""
        sources: list[str] = []
        if self.mode == MODE_SUM or self.mode == "difference":
            sources.extend(self.source_entity_ids)
        else:
            if self.total_entity_id:
                sources.append(self.total_entity_id)
            sources.extend(self.part_entity_ids)
        if not sources and self.source_entity_id:
            sources.append(self.source_entity_id)
        return list(dict.fromkeys(sources))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serialisable representation."""
        return _serializable(
            {
                key: getattr(self, key)
                for key in self.__slots__  # type: ignore[attr-defined]
            }
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SensorDefinition:
        """Create a definition from stored (JSON) data, ignoring unknown keys.

        Values are normalised to the declared field types, because the options
        flow stores what the number selectors returned (``precision: 3.0``).
        """
        valid = set(cls.__slots__)  # type: ignore[attr-defined]
        values = {key: value for key, value in data.items() if key in valid}
        _coerce(
            values,
            ints={
                "confirm_scans": DEFAULT_CONFIRM_SCANS,
                "recovery_hold_scans": DEFAULT_RECOVERY_HOLD_SCANS,
                "grace_period": DEFAULT_GRACE_PERIOD,
                "precision": DEFAULT_PRECISION,
            },
            floats={
                "scale": 1.0,
                "offset": 0.0,
                "zero_min_previous": DEFAULT_ZERO_MIN_PREVIOUS,
                "large_jump": DEFAULT_LARGE_JUMP,
                "large_jump_ratio": DEFAULT_LARGE_JUMP_RATIO,
            },
            optional_floats=("max_value",),
            bools={
                "enabled": True,
                "do_not_decrease": DEFAULT_DO_NOT_DECREASE,
                "accept_real_reset": DEFAULT_ACCEPT_RESET,
                "reject_large_jumps": DEFAULT_REJECT_LARGE_JUMPS,
                "require_all_sources": True,
            },
            strings={
                "id": "",
                "name": "",
                "mode": MODE_SUM,
                "unit_of_measurement": DEFAULT_UNIT,
                "source_entity_id": None,
                "source_unit": None,
                "total_entity_id": None,
            },
            string_lists=("source_entity_ids", "part_entity_ids"),
        )
        return cls(**values)


@dataclass(slots=True)
class UtilityMeterDefinition:
    """Configuration used for utility meter calibration."""

    id: str
    name: str
    utility_meter_entity_id: str
    source_entity_id: str | None = None
    baseline_value: float = 0.0
    baseline_at: str | None = None
    cycle: str | None = None
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serialisable representation."""
        return _serializable(
            {key: getattr(self, key) for key in self.__slots__}  # type: ignore[attr-defined]
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UtilityMeterDefinition:
        """Create a definition from stored (JSON) data."""
        valid = set(cls.__slots__)  # type: ignore[attr-defined]
        values = {key: value for key, value in data.items() if key in valid}
        _coerce(
            values,
            floats={"baseline_value": 0.0},
            bools={"enabled": True},
            strings={
                "id": "",
                "name": "",
                "utility_meter_entity_id": "",
                "source_entity_id": None,
                "baseline_at": None,
                "cycle": None,
            },
        )
        return cls(**values)


@dataclass(slots=True)
class DetectionRules:
    """Detection thresholds used by the live guard and the statistics scanner."""

    scan_interval: int = 300
    lookback_hours: int = 24
    issue_window_hours: int = 24
    simultaneous_threshold: int = 2
    simultaneous_window_minutes: int = 5
    statistics_jump_threshold: float = 100.0
    statistics_jump_ratio: float = 20.0
    sum_state_ratio: float = 10.0
    event_retention_days: int = 14
    scan_scope: str = SCAN_SCOPE_LINKED

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serialisable representation."""
        return {key: getattr(self, key) for key in self.__slots__}  # type: ignore[attr-defined]

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> DetectionRules:
        """Create detection rules from stored data (normalised to the types)."""
        data = data or {}
        valid = set(cls.__slots__)  # type: ignore[attr-defined]
        values = {key: value for key, value in data.items() if key in valid}
        _coerce(
            values,
            ints={
                "scan_interval": 300,
                "lookback_hours": 24,
                "issue_window_hours": 24,
                "simultaneous_threshold": 2,
                "simultaneous_window_minutes": 5,
                "event_retention_days": 14,
            },
            floats={
                "statistics_jump_threshold": 100.0,
                "statistics_jump_ratio": 20.0,
                "sum_state_ratio": 10.0,
            },
            strings={"scan_scope": SCAN_SCOPE_LINKED},
        )
        if values.get("scan_scope") not in SCAN_SCOPES:
            values["scan_scope"] = SCAN_SCOPE_LINKED
        return cls(**values)


@dataclass(slots=True)
class CostRepairConfig:
    """Fixed tariff configuration used to mirror energy repairs into cost statistics."""

    enabled: bool = False
    price: float = 0.0
    currency: str = "GEL"
    energy_statistic_id: str | None = None
    cost_statistic_id: str | None = None
    price_entity_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serialisable representation."""
        return {key: getattr(self, key) for key in self.__slots__}  # type: ignore[attr-defined]

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CostRepairConfig:
        """Create cost configuration from stored data (normalised)."""
        data = data or {}
        valid = set(cls.__slots__)  # type: ignore[attr-defined]
        values = {key: value for key, value in data.items() if key in valid}
        _coerce(
            values,
            floats={"price": 0.0},
            bools={"enabled": False},
            strings={
                "currency": "GEL",
                "energy_statistic_id": None,
                "cost_statistic_id": None,
                "price_entity_id": None,
            },
        )
        return cls(**values)


@dataclass(slots=True)
class BackupConfig:
    """Where backups and reports are written."""

    create_backup: bool = True
    directory: str = "energy_guard"
    keep_backups: int = 25

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serialisable representation."""
        return {key: getattr(self, key) for key in self.__slots__}  # type: ignore[attr-defined]

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> BackupConfig:
        """Create backup configuration from stored data (normalised)."""
        data = data or {}
        valid = set(cls.__slots__)  # type: ignore[attr-defined]
        values = {key: value for key, value in data.items() if key in valid}
        _coerce(
            values,
            ints={"keep_backups": 25},
            bools={"create_backup": True},
            strings={"directory": "energy_guard"},
        )
        return cls(**values)


@dataclass(slots=True)
class AnomalyEvent:
    """A single entry of the Energy Guard diagnostic log."""

    kind: str
    severity: str
    timestamp: datetime
    source_entity: str | None = None
    protected_entity: str | None = None
    previous_value: float | None = None
    invalid_value: float | None = None
    restored_value: float | None = None
    estimated_false_energy: float | None = None
    unit: str = DEFAULT_UNIT
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serialisable representation."""
        return _serializable(
            {
                "kind": self.kind,
                "severity": self.severity,
                "timestamp": self.timestamp,
                "source_entity": self.source_entity,
                "protected_entity": self.protected_entity,
                "previous_value": self.previous_value,
                "invalid_value": self.invalid_value,
                "restored_value": self.restored_value,
                "estimated_false_energy": self.estimated_false_energy,
                "unit": self.unit,
                "message": self.message,
                "details": self.details,
            }
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnomalyEvent:
        """Create an event from stored data."""
        payload = dict(data)
        timestamp = payload.get("timestamp")
        if isinstance(timestamp, str):
            payload["timestamp"] = datetime.fromisoformat(timestamp)
        elif timestamp is None:
            payload["timestamp"] = datetime.min
        return cls(**payload)


@dataclass(slots=True)
class ScanCandidate:
    """A suspicious offset detected in recorder statistics (read-only scan)."""

    statistic_id: str
    start_time: datetime
    detected_at: datetime
    unit: str
    offset: float
    observed_delta: float | None = None
    expected_delta: float | None = None
    estimated_false_energy: float | None = None
    evidence: list[str] = field(default_factory=list)
    severity: str = "warning"
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serialisable representation."""
        return _serializable(
            {
                "statistic_id": self.statistic_id,
                "start_time": self.start_time,
                "detected_at": self.detected_at,
                "unit": self.unit,
                "offset": self.offset,
                "observed_delta": self.observed_delta,
                "expected_delta": self.expected_delta,
                "estimated_false_energy": self.estimated_false_energy,
                "evidence": self.evidence,
                "severity": self.severity,
                "fingerprint": self.fingerprint,
            }
        )


@dataclass(slots=True)
class RepairRequest:
    """A single, explicitly supplied and confirmed offset repair."""

    statistic_id: str
    start_time: datetime
    offset: float
    unit: str | None = None
    reason: str = ""
    fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serialisable representation."""
        return _serializable(
            {
                "statistic_id": self.statistic_id,
                "start_time": self.start_time,
                "offset": self.offset,
                "unit": self.unit,
                "reason": self.reason,
                "fingerprint": self.fingerprint,
            }
        )


@dataclass(slots=True)
class RepairOutcome:
    """Result of applying (or previewing) a single repair."""

    statistic_id: str
    start_time: datetime
    offset: float
    unit: str
    applied: bool
    backup_file: str | None = None
    before_value: float | None = None
    after_value: float | None = None
    expected_value: float | None = None
    verified: bool | None = None
    error: str | None = None
    rollback_adjustment: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serialisable representation."""
        return _serializable(
            {
                "statistic_id": self.statistic_id,
                "start_time": self.start_time,
                "offset": self.offset,
                "unit": self.unit,
                "applied": self.applied,
                "backup_file": self.backup_file,
                "before_value": self.before_value,
                "after_value": self.after_value,
                "expected_value": self.expected_value,
                "verified": self.verified,
                "error": self.error,
                "rollback_adjustment": self.rollback_adjustment,
            }
        )


@dataclass(slots=True)
class EnergyGuardConfig:
    """The complete Energy Guard configuration of one config entry."""

    protected: list[SensorDefinition] = field(default_factory=list)
    derived: list[SensorDefinition] = field(default_factory=list)
    utility_meters: list[UtilityMeterDefinition] = field(default_factory=list)
    detection: DetectionRules = field(default_factory=DetectionRules)
    cost: CostRepairConfig = field(default_factory=CostRepairConfig)
    backups: BackupConfig = field(default_factory=BackupConfig)

    @property
    def all_definitions(self) -> list[SensorDefinition]:
        """Return enabled protected and derived definitions."""
        return [
            definition
            for definition in (*self.protected, *self.derived)
            if definition.enabled
        ]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serialisable representation."""
        return {
            "protected_sensors": [item.to_dict() for item in self.protected],
            "derived_sensors": [item.to_dict() for item in self.derived],
            "utility_meters": [item.to_dict() for item in self.utility_meters],
            "detection_rules": self.detection.to_dict(),
            "cost_repair": self.cost.to_dict(),
            "backups_reports": self.backups.to_dict(),
        }


def build_anomaly_event(
    kind: str,
    severity: str,
    message: str,
    **kwargs: Any,
) -> AnomalyEvent:
    """Build an :class:`AnomalyEvent` with the current timestamp.

    Kept here (instead of in the entity code) so every caller writes the same
    field set into the diagnostic log.
    """
    return AnomalyEvent(
        kind=kind,
        severity=severity,
        timestamp=datetime.now(UTC),
        message=message,
        **kwargs,
    )
