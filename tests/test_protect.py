"""Unit tests for the protection engine (no Home Assistant required)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.energy_guard.const import (
    EVENT_DECREASE_BLOCKED,
    EVENT_LARGE_JUMP,
    EVENT_RESET_ACCEPTED,
    EVENT_SOURCE_INVALID,
    EVENT_ZERO_RESET_BLOCKED,
    REASON_DECREASE_HELD,
    REASON_FIRST_VALUE,
    REASON_INVALID_SOURCE,
    REASON_JUMP_HELD,
    REASON_MAX_HELD,
    REASON_OK,
    REASON_RECOVERY_HOLD,
    REASON_RESET_ACCEPTED,
    REASON_ZERO_HELD,
)
from custom_components.energy_guard.models import SensorDefinition
from custom_components.energy_guard.protect import SensorGuard, parse_float

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def definition(**overrides) -> SensorDefinition:
    """Return a definition with sane test defaults."""
    values = {
        "id": "test",
        "name": "Test sensor",
        "source_entity_id": "sensor.meter",
        "unit_of_measurement": "kWh",
        "grace_period": 0,
        "large_jump": 1000.0,
        "large_jump_ratio": 1000.0,
    }
    values.update(overrides)
    return SensorDefinition(**values)


def run(guard: SensorGuard, values: list[str | float | None], step: int = 10):
    """Feed a sequence of states into a guard."""
    outcomes = []
    for index, value in enumerate(values):
        raw = value if isinstance(value, str) or value is None else str(value)
        outcomes.append(guard.evaluate(raw, NOW + timedelta(seconds=index * step)))
    return outcomes


# ---------------------------------------------------------------------------
# value parsing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10", 10.0),
        ("10.5", 10.5),
        (10, 10.0),
        (10.5, 10.5),
        ("unavailable", None),
        ("unknown", None),
        ("", None),
        (None, None),
        ("not-a-number", None),
        (True, None),
        (False, None),
    ],
)
def test_parse_float(value, expected) -> None:
    """Only real numbers are accepted."""
    assert parse_float(value) == expected


# ---------------------------------------------------------------------------
# reconnect protection
# ---------------------------------------------------------------------------
def test_first_value_is_published() -> None:
    """The first reading becomes the baseline."""
    guard = SensorGuard(definition())
    outcome = guard.evaluate("38243.46", NOW)
    assert outcome.publish is True
    assert outcome.value == 38243.46
    assert outcome.reason == REASON_FIRST_VALUE
    assert guard.last_good == 38243.46


def test_unavailable_holds_last_good_value() -> None:
    """An unavailable source never resets the protected value."""
    guard = SensorGuard(definition())
    run(guard, ["38243.46", "unavailable"])
    outcome = run(guard, ["unavailable"])[0]
    assert outcome.publish is False
    assert outcome.available is False
    assert outcome.reason == REASON_INVALID_SOURCE
    assert guard.last_good == 38243.46


def test_unavailable_to_zero_to_restored_value() -> None:
    """The exact reconnect sequence must never emit a false zero."""
    guard = SensorGuard(definition())
    outcomes = run(
        guard,
        ["38243.46", "unavailable", "unavailable", "0.0", "0.0", "38243.9"],
    )
    published = [outcome.value for outcome in outcomes if outcome.publish]

    assert published == [38243.46, 38243.9]
    assert outcomes[3].reason == REASON_ZERO_HELD
    assert outcomes[3].available is False
    assert outcomes[4].reason == REASON_ZERO_HELD
    assert outcomes[5].publish is True
    # The estimated false energy is what the recorder would have added.
    assert outcomes[3].event is not None
    assert outcomes[3].event.kind == EVENT_ZERO_RESET_BLOCKED
    assert outcomes[3].event.estimated_false_energy == pytest.approx(38243.46)
    assert outcomes[4].event is not None
    assert outcomes[4].event.kind == EVENT_ZERO_RESET_BLOCKED


def test_zero_of_a_small_meter_is_not_suspicious() -> None:
    """A meter that is really at zero may report zero."""
    guard = SensorGuard(definition(zero_min_previous=1.0))
    outcomes = run(guard, ["0.5", "0.0", "0.2"])
    assert [outcome.publish for outcome in outcomes] == [True, True, True]


def test_false_cumulative_reset_is_blocked() -> None:
    """5000 -> 0 -> 5000.5 must not look like a reset."""
    guard = SensorGuard(definition(do_not_decrease=True))
    outcomes = run(guard, ["5000.0", "0.0", "5000.5"])
    assert outcomes[1].reason == REASON_ZERO_HELD
    assert outcomes[1].publish is False
    assert outcomes[2].publish is True
    assert outcomes[2].value == 5000.5


def test_do_not_decrease_blocks_partial_drops() -> None:
    """A drop to a non-zero value is held as well."""
    guard = SensorGuard(definition(do_not_decrease=True))
    outcomes = run(guard, ["5000.0", "1000.0"])
    assert outcomes[1].publish is False
    assert outcomes[1].reason == REASON_DECREASE_HELD
    assert outcomes[1].event is not None
    assert outcomes[1].event.kind == EVENT_DECREASE_BLOCKED
    assert outcomes[1].event.estimated_false_energy == pytest.approx(4000.0)


def test_do_not_decrease_disabled_accepts_drop() -> None:
    """Users can turn the guard off per sensor."""
    guard = SensorGuard(definition(do_not_decrease=False))
    outcomes = run(guard, ["5000.0", "1000.0", "1000.5"])
    assert [outcome.publish for outcome in outcomes] == [True, True, True]
    assert outcomes[1].value == 1000.0


def test_small_backwards_correction_is_accepted() -> None:
    """Rounding corrections are not treated as a reconnect."""
    guard = SensorGuard(definition())
    outcomes = run(guard, ["1000.0", "999.999"])
    assert outcomes[1].publish is True


def test_real_reset_needs_confirmation() -> None:
    """With accept_real_reset the reset is only taken after confirmation."""
    guard = SensorGuard(definition(accept_real_reset=True, confirm_scans=3))
    outcomes = run(guard, ["5000.0", "0.0", "0.0", "0.0", "0.4"])
    assert outcomes[1].publish is False
    assert outcomes[2].publish is False
    assert outcomes[3].publish is True
    assert outcomes[3].reason == REASON_RESET_ACCEPTED
    assert outcomes[3].event is not None
    assert outcomes[3].event.kind == EVENT_RESET_ACCEPTED
    assert outcomes[4].value == 0.4


def test_recovery_hold_scans_delays_publication() -> None:
    """After a long gap the sensor waits for stable readings."""
    guard = SensorGuard(definition(grace_period=10, recovery_hold_scans=2))
    guard.evaluate("1000.0", NOW)
    # A gap of 60 s (>= grace period) followed by a sane value.
    outcome = guard.evaluate("unavailable", NOW + timedelta(seconds=10))
    assert outcome.publish is False
    outcome = guard.evaluate("1000.5", NOW + timedelta(seconds=70))
    assert outcome.reason == REASON_RECOVERY_HOLD
    assert outcome.publish is False
    outcome = guard.evaluate("1000.6", NOW + timedelta(seconds=80))
    assert outcome.publish is True


def test_large_jump_is_reported_and_can_be_rejected() -> None:
    """Implausible jumps are logged; blocking is opt-in."""
    guard = SensorGuard(
        definition(large_jump=100.0, large_jump_ratio=1.0, reject_large_jumps=True)
    )
    guard.evaluate("10.0", NOW)
    outcome = guard.evaluate("500.0", NOW + timedelta(seconds=10))
    assert outcome.publish is False
    assert outcome.reason == REASON_JUMP_HELD
    assert outcome.event is not None
    assert outcome.event.kind == EVENT_LARGE_JUMP
    # estimated false energy = jump - expected increase (no history yet -> 490)
    assert outcome.event.estimated_false_energy == pytest.approx(490.0)


def test_large_jump_reported_but_published_by_default() -> None:
    """By default Energy Guard only reports, it does not block real jumps."""
    guard = SensorGuard(definition(large_jump=100.0, large_jump_ratio=1.0))
    guard.evaluate("10.0", NOW)
    outcome = guard.evaluate("500.0", NOW + timedelta(seconds=10))
    assert outcome.publish is True
    assert outcome.event is not None
    assert outcome.event.kind == EVENT_LARGE_JUMP


def test_invalid_source_event_is_reported_once() -> None:
    """Only one event is logged per outage."""
    guard = SensorGuard(definition())
    outcomes = run(guard, ["100.0", "unavailable", "unavailable", "unavailable"])
    events = [outcome.event for outcome in outcomes if outcome.event is not None]
    assert len(events) == 1
    assert events[0].kind == EVENT_SOURCE_INVALID


def test_offset_and_precision() -> None:
    """The baseline offset is applied to the published value."""
    guard = SensorGuard(definition(offset=100.0, precision=2))
    outcome = guard.evaluate("5.678", NOW)
    assert outcome.value == 105.68


def test_units_are_converted() -> None:
    """A source in Wh is published in kWh."""
    guard = SensorGuard(definition(source_unit="Wh", unit_of_measurement="kWh"))
    outcome = guard.evaluate("1500", NOW)
    assert outcome.value == pytest.approx(1.5)


def test_max_value_guard() -> None:
    """An implausible high reading is blocked when a maximum is configured."""
    guard = SensorGuard(definition(max_value=10000.0))
    run(guard, ["100.0"])
    outcome = run(guard, ["99999.0"])[0]
    assert outcome.publish is False
    assert outcome.reason == REASON_MAX_HELD


def test_restore_seeds_the_guard() -> None:
    """A restored value prevents a false zero right after a restart."""
    guard = SensorGuard(definition())
    guard.seed(38243.46, NOW)
    outcome = guard.evaluate("0.0", NOW + timedelta(seconds=10))
    assert outcome.publish is False
    assert outcome.reason == REASON_ZERO_HELD


def test_reason_ok_for_normal_increases() -> None:
    """A normal increase is published without an event."""
    guard = SensorGuard(definition())
    run(guard, ["100.0"])
    outcome = run(guard, ["100.5"])[0]
    assert outcome.reason == REASON_OK
    assert outcome.publish is True
