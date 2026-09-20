"""Data model tests: type coercion of stored configuration.

The options flow stores whatever the Home Assistant selectors return, and a
number selector returns a float even for an integer field (``precision: 3.0``).
These models are the boundary between "what the UI stored" and "what the code
needs", so the coercion is tested here - a float in an integer field used to
crash the guard (``round(value, 3.0)`` -> ``TypeError``) and silently prevented
the protected sensor from being created.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.energy_guard.const import (
    CONF_ACCEPT_RESET,
    CONF_CONFIRM_SCANS,
    CONF_GRACE_PERIOD,
    CONF_MAX_VALUE,
    CONF_OFFSET,
    CONF_PRECISION,
    CONF_RECOVERY_HOLD_SCANS,
    CONF_SOURCE,
    CONF_TARGET_UNIT,
    CONF_ZERO_MIN_PREVIOUS,
    DEFAULT_LARGE_JUMP,
    MODE_PHASE_SPLIT,
)
from custom_components.energy_guard.models import (
    BackupConfig,
    CostRepairConfig,
    DetectionRules,
    SensorDefinition,
    UtilityMeterDefinition,
)
from custom_components.energy_guard.protect import SensorGuard, parse_float

# Exactly what the options flow stores for a GUI configured protected sensor:
# every number is a float, even the ones that are semantically integers.
STORED_FROM_FLOW = {
    "name": "Grid import protected",
    CONF_SOURCE: "sensor.grid_import",
    CONF_TARGET_UNIT: "kWh",
    CONF_OFFSET: 2.5,
    "enabled": True,
    "do_not_decrease": True,
    CONF_ACCEPT_RESET: False,
    CONF_ZERO_MIN_PREVIOUS: 1.0,
    CONF_CONFIRM_SCANS: 2.0,
    CONF_RECOVERY_HOLD_SCANS: 1.0,
    CONF_GRACE_PERIOD: 30.0,
    "large_jump": 100000.0,
    "large_jump_ratio": 1000.0,
    "reject_large_jumps": False,
    CONF_PRECISION: 3.0,
    CONF_MAX_VALUE: 999999.0,
    "id": "abc123",
    "scale": 1.0,
}


def test_sensor_definition_coerces_integer_fields() -> None:
    """Integer fields arriving as floats are stored as ints."""
    definition = SensorDefinition.from_dict(STORED_FROM_FLOW)

    assert definition.precision == 3
    assert isinstance(definition.precision, int)
    assert definition.confirm_scans == 2
    assert definition.recovery_hold_scans == 1
    assert definition.grace_period == 30
    for value in (
        definition.precision,
        definition.confirm_scans,
        definition.recovery_hold_scans,
        definition.grace_period,
    ):
        assert isinstance(value, int)
    # Floats stay floats, including the optional ones.
    assert definition.offset == 2.5
    assert definition.scale == 1.0
    assert definition.max_value == 999999.0
    assert definition.zero_min_previous == 1.0
    assert definition.name == "Grid import protected"
    assert definition.source_entity_id == "sensor.grid_import"


def test_guard_accepts_a_definition_stored_by_the_options_flow() -> None:
    """Regression: a UI configured sensor must not crash the protection engine.

    ``SensorGuard._output`` calls ``round(value, definition.precision)``; with a
    float precision from the options flow this raised ``TypeError`` and aborted
    the entity setup, so the protected sensor never appeared.
    """
    definition = SensorDefinition.from_dict(STORED_FROM_FLOW)
    guard = SensorGuard(definition)
    now = datetime(2026, 9, 18, 4, 0, tzinfo=UTC)

    outcome = guard.evaluate("38243.46", now)

    assert outcome.publish is True
    assert outcome.value == pytest.approx(38245.96)  # 38243.46 + the offset


def test_sensor_definition_defaults_and_unknown_keys() -> None:
    """Missing fields fall back to the defaults; unknown keys are ignored."""
    definition = SensorDefinition.from_dict(
        {"id": "d1", "name": "x", CONF_SOURCE: "sensor.a", "not_a_field": 1}
    )
    assert definition.mode == "sum"
    assert definition.unit_of_measurement == "kWh"
    assert definition.precision == 3
    assert not hasattr(definition, "not_a_field")


def test_sensor_definition_normalises_booleans_and_lists() -> None:
    """Booleans and entity lists are normalised, duplicates removed."""
    definition = SensorDefinition.from_dict(
        {
            "id": "d2",
            "name": "Phases",
            "mode": MODE_PHASE_SPLIT,
            "enabled": "false",
            "require_all_sources": 0,
            "total_entity_id": "  sensor.total  ",
            "part_entity_ids": ["sensor.a", "sensor.a", "", None, "sensor.b"],
            "source_entity_ids": None,
        }
    )
    assert definition.enabled is False
    assert definition.require_all_sources is False
    assert definition.total_entity_id == "sensor.total"
    assert definition.part_entity_ids == ["sensor.a", "sensor.b"]
    assert definition.source_entity_ids == []


def test_sensor_definition_rejects_non_finite_values() -> None:
    """``nan``/``inf`` must not reach a comparison: they fall back to defaults."""
    definition = SensorDefinition.from_dict(
        {
            "id": "d3",
            "name": "Broken",
            CONF_OFFSET: float("inf"),
            "large_jump": float("nan"),
            CONF_MAX_VALUE: float("inf"),
            CONF_PRECISION: float("nan"),
        }
    )
    assert definition.offset == 0.0
    assert definition.large_jump == pytest.approx(DEFAULT_LARGE_JUMP)
    assert definition.max_value is None
    assert definition.precision == 3


def test_other_models_coerce_ui_values() -> None:
    """Utility meter, detection, cost and backup settings are coerced too."""
    meter = UtilityMeterDefinition.from_dict(
        {
            "id": "m1",
            "name": "Grid import monthly",
            "utility_meter_entity_id": "sensor.grid_import_monthly",
            "source_entity_id": "sensor.grid_import_protected",
            "baseline_value": 38000.0,
            "cycle": "",
            "enabled": "True",
        }
    )
    assert meter.baseline_value == 38000.0
    assert meter.cycle is None
    assert meter.enabled is True

    detection = DetectionRules.from_dict(
        {
            "scan_interval": 90.0,
            "lookback_hours": 48.0,
            "event_retention_days": 14.0,
            "statistics_jump_threshold": "500",
            "sum_state_ratio": 10.0,
        }
    )
    assert detection.scan_interval == 90
    assert detection.lookback_hours == 48
    assert detection.event_retention_days == 14
    assert all(
        isinstance(value, int)
        for value in (
            detection.scan_interval,
            detection.lookback_hours,
            detection.simultaneous_threshold,
            detection.event_retention_days,
        )
    )
    assert detection.statistics_jump_threshold == 500.0

    cost = CostRepairConfig.from_dict(
        {
            "enabled": "yes",
            "price": "0.235",
            "currency": "GEL",
            "energy_statistic_id": "sensor.grid_import",
            "cost_statistic_id": "sensor.grid_import_cost",
            "price_entity_id": "",
        }
    )
    assert cost.enabled is True
    assert cost.price == 0.235
    assert cost.currency == "GEL"
    assert cost.price_entity_id is None

    backups = BackupConfig.from_dict({"create_backup": 1, "keep_backups": 25.0})
    assert backups.create_backup is True
    assert backups.keep_backups == 25
    assert isinstance(backups.keep_backups, int)
    assert backups.directory == "energy_guard"


def test_backup_pruning_survives_an_options_flow_keep_value(tmp_path) -> None:
    """``keep_backups`` arrives as a float from the UI but is used for slicing."""
    from custom_components.energy_guard.backups import prune_backups

    for index in range(5):
        (
            tmp_path
            / f"statistics_backup_2026091{index}T040000Z_sensor_grid_import.json"
        ).write_text("{}")

    keep = BackupConfig.from_dict({"keep_backups": 3.0}).keep_backups
    assert isinstance(keep, int)
    prune_backups(tmp_path, keep)

    remaining = sorted(path.name for path in tmp_path.glob("*.json"))
    assert len(remaining) == 3
    # The newest files are the ones that survive (mtime based pruning).
    assert remaining[-1].endswith("20260914T040000Z_sensor_grid_import.json")


def test_parse_float_still_rejects_junk() -> None:
    """The safe parser is part of the same contract."""
    assert parse_float("38243.46") == pytest.approx(38243.46)
    assert parse_float("38123,46") is None
    assert parse_float("nan") is None
    assert parse_float("inf") is None
    assert parse_float(None) is None
    assert parse_float("unavailable") is None


def test_definition_round_trip_keeps_types() -> None:
    """to_dict/from_dict round trips preserve the coerced types."""
    definition = SensorDefinition.from_dict(STORED_FROM_FLOW)
    again = SensorDefinition.from_dict(definition.to_dict())
    assert again.precision == definition.precision
    assert isinstance(again.precision, int)
    assert again.to_dict() == definition.to_dict()
    # A window long enough for the grace period of the seeded definition.
    assert definition.grace_period == 30
    assert timedelta(seconds=definition.grace_period) == timedelta(seconds=30)
