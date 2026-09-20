"""Constants for the Energy Guard integration.

Energy Guard protects ``total_increasing`` energy sensors (and the Home Assistant
Energy Dashboard statistics that are derived from them) against corrupt data
caused by devices that briefly report ``unavailable`` or ``0`` during a
reconnect, and then jump back to their previous lifetime value.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "energy_guard"
NAME: Final = "Energy Guard"
VERSION: Final = "1.1.0"

MANUFACTURER: Final = "Energy Guard"
MODEL: Final = "Energy Guard"

PLATFORMS: Final = ["sensor", "binary_sensor"]

# ---------------------------------------------------------------------------
# Config entry / options keys
# ---------------------------------------------------------------------------
CONF_PROTECTED: Final = "protected_sensors"
CONF_DERIVED: Final = "derived_sensors"
CONF_UTILITY_METERS: Final = "utility_meters"
CONF_DETECTION: Final = "detection_rules"
CONF_STATISTICS: Final = "statistics_repair"
CONF_COST: Final = "cost_repair"
CONF_BACKUPS: Final = "backups_reports"

# ---------------------------------------------------------------------------
# Configuration sections (shared by the options flow and the config panel)
# ---------------------------------------------------------------------------
SECTION_PROTECTED: Final = "protected_sensors"
SECTION_DERIVED: Final = "derived_sensors"
SECTION_METERS: Final = "utility_meters"
SECTION_DETECTION: Final = "detection_rules"
SECTION_STATISTICS: Final = "statistics_repair"
SECTION_COST: Final = "cost_repair"
SECTION_BACKUPS: Final = "backups_reports"
SECTION_REVIEW: Final = "review"
SECTION_EXPORT: Final = "export_yaml"
#: Every section, in menu order.
SECTIONS: Final = (
    SECTION_PROTECTED,
    SECTION_DERIVED,
    SECTION_METERS,
    SECTION_DETECTION,
    SECTION_STATISTICS,
    SECTION_COST,
    SECTION_BACKUPS,
    SECTION_REVIEW,
    SECTION_EXPORT,
)
#: Sections that store a list of definitions (protected/derived/meters).
CONF_DEFINITION_SECTIONS: Final = (
    SECTION_PROTECTED,
    SECTION_DERIVED,
    SECTION_METERS,
)
#: Sections that store a settings dictionary.
CONF_SETTINGS_SECTIONS: Final = (
    SECTION_DETECTION,
    SECTION_STATISTICS,
    SECTION_COST,
    SECTION_BACKUPS,
)

# ---------------------------------------------------------------------------
# Sensor definition keys
# ---------------------------------------------------------------------------
CONF_ID: Final = "id"
CONF_NAME: Final = "name"
CONF_ENABLED: Final = "enabled"
CONF_SOURCE: Final = "source_entity_id"
CONF_SOURCES: Final = "source_entity_ids"
CONF_SOURCE_UNIT: Final = "source_unit"
CONF_TARGET_UNIT: Final = "unit_of_measurement"
CONF_OFFSET: Final = "offset"
CONF_DO_NOT_DECREASE: Final = "do_not_decrease"
CONF_ACCEPT_RESET: Final = "accept_real_reset"
CONF_ZERO_MIN_PREVIOUS: Final = "zero_min_previous"
CONF_CONFIRM_SCANS: Final = "confirm_scans"
CONF_RECOVERY_HOLD_SCANS: Final = "recovery_hold_scans"
CONF_GRACE_PERIOD: Final = "grace_period"
CONF_LARGE_JUMP: Final = "large_jump"
CONF_LARGE_JUMP_RATIO: Final = "large_jump_ratio"
CONF_REJECT_LARGE_JUMPS: Final = "reject_large_jumps"
CONF_MAX_VALUE: Final = "max_value"
CONF_PRECISION: Final = "precision"
CONF_DEVICE_CLASS: Final = "device_class"

# Derived sensor keys
CONF_MODE: Final = "mode"
CONF_PARTS: Final = "part_entity_ids"
CONF_TOTAL: Final = "total_entity_id"
CONF_SCALE: Final = "scale"
CONF_REQUIRE_ALL_SOURCES: Final = "require_all_sources"

MODE_SUM: Final = "sum"
MODE_DIFFERENCE: Final = "difference"
MODE_PHASE_SPLIT: Final = "phase_split"
DERIVED_MODES: Final = (MODE_SUM, MODE_DIFFERENCE, MODE_PHASE_SPLIT)

# Utility meter definition keys
CONF_UTILITY_METER: Final = "utility_meter_entity_id"
CONF_BASELINE: Final = "baseline_value"
CONF_BASELINE_AT: Final = "baseline_at"
CONF_CYCLE: Final = "cycle"

# Cost repair keys
CONF_PRICE: Final = "price"
CONF_CURRENCY: Final = "currency"
CONF_ENERGY_STATISTIC_ID: Final = "energy_statistic_id"
CONF_COST_STATISTIC_ID: Final = "cost_statistic_id"
CONF_PRICE_ENTITY: Final = "price_entity_id"

# ---------------------------------------------------------------------------
# Detection / scanner keys
# ---------------------------------------------------------------------------
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_LOOKBACK_HOURS: Final = "lookback_hours"
CONF_ISSUE_WINDOW_HOURS: Final = "issue_window_hours"
CONF_SIMULTANEOUS_THRESHOLD: Final = "simultaneous_threshold"
CONF_SIMULTANEOUS_WINDOW_MINUTES: Final = "simultaneous_window_minutes"
CONF_STAT_JUMP_KWH: Final = "statistics_jump_threshold"
CONF_STAT_JUMP_RATIO: Final = "statistics_jump_ratio"
CONF_STAT_SUM_STATE_RATIO: Final = "sum_state_ratio"
CONF_EVENT_RETENTION_DAYS: Final = "event_retention_days"
CONF_SCAN_SCOPE: Final = "scan_scope"

DEFAULT_SCAN_INTERVAL: Final = 300
DEFAULT_LOOKBACK_HOURS: Final = 24
DEFAULT_ISSUE_WINDOW_HOURS: Final = 24
DEFAULT_SIMULTANEOUS_THRESHOLD: Final = 2
DEFAULT_SIMULTANEOUS_WINDOW_MINUTES: Final = 5
DEFAULT_STAT_JUMP_KWH: Final = 100.0
DEFAULT_STAT_JUMP_RATIO: Final = 20.0
DEFAULT_STAT_SUM_STATE_RATIO: Final = 10.0
DEFAULT_EVENT_RETENTION_DAYS: Final = 14

# ---------------------------------------------------------------------------
# Statistics scan scope: which statistics a scan covers
# ---------------------------------------------------------------------------
#: Only the statistics Energy Guard is linked to (protected sources, derived
#: sensors, utility meter sources and the configured cost statistics).
SCAN_SCOPE_LINKED: Final = "linked"
#: Every cumulative statistic that carries an energy unit (kWh, Wh, MWh, ...).
SCAN_SCOPE_ENERGY: Final = "energy"
#: Every cumulative statistic in the recorder, whatever its unit.
SCAN_SCOPE_ALL: Final = "all"
#: Not a user selectable scope: reported back when explicit ids were scanned.
SCAN_SCOPE_EXPLICIT: Final = "explicit"
#: Scopes a user may select.
SCAN_SCOPES: Final = (SCAN_SCOPE_LINKED, SCAN_SCOPE_ENERGY, SCAN_SCOPE_ALL)
DEFAULT_SCAN_SCOPE: Final = SCAN_SCOPE_LINKED
#: Hard cap so a whole recorder scan stays a predictable amount of work.
MAX_DISCOVERED_STATISTICS: Final = 500

# ---------------------------------------------------------------------------
# Protection defaults
# ---------------------------------------------------------------------------
DEFAULT_DO_NOT_DECREASE: Final = True
DEFAULT_ACCEPT_RESET: Final = False
DEFAULT_ZERO_MIN_PREVIOUS: Final = 1.0
DEFAULT_CONFIRM_SCANS: Final = 2
DEFAULT_RECOVERY_HOLD_SCANS: Final = 1
DEFAULT_GRACE_PERIOD: Final = 30
DEFAULT_LARGE_JUMP: Final = 100.0
DEFAULT_LARGE_JUMP_RATIO: Final = 20.0
DEFAULT_REJECT_LARGE_JUMPS: Final = False
DEFAULT_PRECISION: Final = 3
DEFAULT_UNIT: Final = "kWh"
MAX_EVENTS_STORED: Final = 250
STORAGE_VERSION: Final = 1

# ---------------------------------------------------------------------------
# Anomaly / event types
# ---------------------------------------------------------------------------
EVENT_ZERO_RESET_BLOCKED: Final = "zero_reset_blocked"
EVENT_DECREASE_BLOCKED: Final = "decrease_blocked"
EVENT_SOURCE_INVALID: Final = "source_invalid"
EVENT_SOURCE_RECOVERED: Final = "source_recovered"
EVENT_LARGE_JUMP: Final = "large_jump"
EVENT_RESET_ACCEPTED: Final = "cumulative_reset_accepted"
EVENT_ABOVE_MAX: Final = "value_above_max"
EVENT_SIMULTANEOUS: Final = "simultaneous_anomaly"
EVENT_STATISTICS_OFFSET: Final = "statistics_offset"
EVENT_REPAIRED: Final = "statistics_repaired"
EVENT_CALIBRATED: Final = "utility_meter_calibrated"
EVENT_CLEARED: Final = "statistics_cleared"
EVENT_RESTORED: Final = "value_restored"

SEVERITY_INFO: Final = "info"
SEVERITY_WARNING: Final = "warning"
SEVERITY_ERROR: Final = "error"

#: Plain language "what should I do now" text for every event Energy Guard can
#: log.  It is copied into the ``details`` of the event, so the diagnostic log,
#: the Repairs page and the exported report all answer the same question.
RECOMMENDED_ACTIONS: Final[dict[str, str]] = {
    EVENT_ZERO_RESET_BLOCKED: (
        "Nothing to fix in the dashboard: the false 0 was blocked and never "
        "reached the statistics. If the meter was genuinely replaced/reset, turn "
        "on 'accept real reset' for this sensor."
    ),
    EVENT_DECREASE_BLOCKED: (
        "Compare the source sensor with the physical meter. A swapped or "
        "mis-read meter needs a baseline offset or 'accept real reset'; a "
        "temporary glitch needs no action."
    ),
    EVENT_SOURCE_INVALID: (
        "Wait for the source to report a number again - Energy Guard publishes "
        "'unavailable' in the meantime, which cannot corrupt statistics. If the "
        "meter keeps dropping, check its connection/gateway."
    ),
    EVENT_SOURCE_RECOVERED: (
        "No action needed: the source is back and the protected sensor follows "
        "it again."
    ),
    EVENT_LARGE_JUMP: (
        "Compare with the meter display. A real jump (new meter, restored "
        "history) is fine; otherwise raise the large jump threshold or repair "
        "the statistics that were written before Energy Guard was installed."
    ),
    EVENT_ABOVE_MAX: (
        "The reading is above the configured maximum value. Check the meter and "
        "the 'max value' setting of this sensor."
    ),
    EVENT_RESET_ACCEPTED: (
        "The reset was confirmed by repeated readings and is published. Add a "
        "baseline offset if the new cycle must continue the old total."
    ),
    EVENT_SIMULTANEOUS: (
        "Several meters failed at the same moment - check the shared gateway, "
        "hub or power supply before repairing anything."
    ),
    EVENT_STATISTICS_OFFSET: (
        "Review the candidate with energy_guard.scan_statistics, then repair it "
        "with energy_guard.repair_statistics and confirm: true. Money needs its "
        "own confirmation (confirm_cost: true)."
    ),
    EVENT_REPAIRED: (
        "No action needed: the offset was applied, backed up and verified. The "
        "response contains the inverse offset if you ever want to undo it."
    ),
    EVENT_CALIBRATED: (
        "No action needed: the utility meter was calibrated and read back."
    ),
    EVENT_CLEARED: (
        "The statistics were cleared on request. The recorder rebuilds them from "
        "the entities that keep reporting; the backup file holds the old rows."
    ),
    EVENT_RESTORED: (
        "No action needed: the source reports its real lifetime value again."
    ),
}

# Reasons returned by the protection engine
REASON_OK: Final = "ok"
REASON_FIRST_VALUE: Final = "first_value"
REASON_INVALID_SOURCE: Final = "source_invalid"
REASON_ZERO_HELD: Final = "zero_held"
REASON_DECREASE_HELD: Final = "decrease_held"
REASON_RECOVERY_HOLD: Final = "recovery_hold"
REASON_JUMP_HELD: Final = "jump_held"
REASON_MAX_HELD: Final = "max_held"
REASON_RESET_ACCEPTED: Final = "reset_accepted"

# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------
SERVICE_SCAN_STATISTICS: Final = "scan_statistics"
SERVICE_REPAIR_STATISTICS: Final = "repair_statistics"
SERVICE_CALIBRATE_UTILITY_METER: Final = "calibrate_utility_meter"
SERVICE_CLEAR_STATISTICS: Final = "clear_statistics"
SERVICE_REPORT: Final = "export_repair_report"
SERVICE_EXPORT_TEMPLATES: Final = "export_templates"

SERVICE_NAMES: Final = (
    SERVICE_SCAN_STATISTICS,
    SERVICE_REPAIR_STATISTICS,
    SERVICE_CALIBRATE_UTILITY_METER,
    SERVICE_CLEAR_STATISTICS,
    SERVICE_REPORT,
    SERVICE_EXPORT_TEMPLATES,
)

# Services that modify data always require an explicit confirmation.
CONFIRMATION_REQUIRED_SERVICES: Final = (
    SERVICE_REPAIR_STATISTICS,
    SERVICE_CLEAR_STATISTICS,
    SERVICE_CALIBRATE_UTILITY_METER,
)

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
ENERGY_UNITS: Final = ("Wh", "kWh", "MWh", "GWh", "MJ", "GJ", "kJ", "BTU")

# Absolute factor to convert a unit into kWh.
ENERGY_UNIT_TO_KWH: Final = {
    "wh": 0.001,
    "kwh": 1.0,
    "mwh": 1000.0,
    "gwh": 1_000_000.0,
    "j": 1.0 / 3_600_000.0,
    "kj": 1.0 / 3600.0,
    "mj": 1000.0 / 3600.0,
    "gj": 1_000_000.0 / 3600.0,
    "btu": 0.0002930710701722222,
}

# Tolerance used when comparing statistic sums before/after a repair.
ALREADY_REPAIRED_TOLERANCE: Final = 0.001

DEFAULT_CURRENCY: Final = "GEL"
DEFAULT_CURRENCY_SYMBOL: Final = "GEL"

# File system layout (inside the Home Assistant configuration directory)
REPORTS_DIR: Final = "reports"
BACKUPS_DIR: Final = "backups"
TEMPLATES_FILE: Final = "energy_guard_templates.yaml"

# Data keys used in hass.data[DOMAIN]
DATA_HUBS: Final = "hubs"
DATA_SERVICES_REGISTERED: Final = "services_registered"
