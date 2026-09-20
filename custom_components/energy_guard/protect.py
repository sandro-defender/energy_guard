"""The Energy Guard protection engine.

The engine is deliberately free of Home Assistant imports: it takes the raw
source state as a string (exactly as it appears in ``hass.states``) plus a
timestamp and decides what the protected sensor is allowed to publish.

The single most important rule: a protected cumulative energy sensor must never
publish a value that the recorder/an Energy Dashboard would interpret as a
legitimate increase when it actually is a reconnect artefact.  In practice that
means:

* ``unavailable`` / ``unknown`` / non numeric -> hold the last good value and
  publish ``unavailable`` (never ``0.0``);
* a sudden drop to ``0`` (or to a much lower value) -> hold, unless the user
  explicitly allowed meter resets *and* the reading is confirmed;
* a jump so large that it cannot be a real increment -> report it, and hold
  when the user asked for that behaviour.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from .const import (
    EVENT_ABOVE_MAX,
    EVENT_DECREASE_BLOCKED,
    EVENT_LARGE_JUMP,
    EVENT_RESET_ACCEPTED,
    EVENT_SOURCE_INVALID,
    EVENT_SOURCE_RECOVERED,
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
    SEVERITY_INFO,
    SEVERITY_WARNING,
)
from .models import AnomalyEvent, SensorDefinition

_LOGGER = logging.getLogger(__name__)

INVALID_STATES: Final = ("", "unknown", "unavailable", "none", "null")

# Exponential moving average weight for "typical" increases.
_EMA_ALPHA: Final = 0.25


def parse_float(value: object) -> float | None:
    """Return ``value`` as float, or ``None`` when it is not a usable number.

    Rejected on purpose:

    * booleans - ``float(True) == 1.0`` would silently turn a broken template
      sensor into a cumulative meter reading,
    * ``nan``/``nan`` strings and ``inf`` - they are floats for Python but
      poison every ``<``/``>`` comparison (NaN is never smaller or greater than
      anything, so a guard that accepts it stops protecting anything), and
      Home Assistant stores a NaN sensor value as ``0.0``.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in INVALID_STATES:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


@dataclass(slots=True)
class GuardOutcome:
    """The decision taken by :class:`SensorGuard` for a single update."""

    publish: bool
    """Whether the caller may write a state."""

    available: bool
    """Whether the protected entity should be available."""

    value: float | None
    """Value to publish (offset applied), ``None`` when holding."""

    raw_value: float | None
    """Numeric source value of this update, if the source was numeric."""

    reason: str
    """Machine readable reason for the decision."""

    last_good: float | None
    """Last accepted (source) value known to the guard."""

    held_since: datetime | None = None
    event: AnomalyEvent | None = None

    @property
    def holding(self) -> bool:
        """Return True when the guard refused the incoming reading."""
        return self.publish is False and self.available is False


class SensorGuard:
    """Stateful monotonic guard for a single cumulative energy sensor."""

    def __init__(self, definition: SensorDefinition) -> None:
        """Initialise the guard for one protected sensor definition."""
        self.definition = definition
        self.last_good: float | None = None
        self.last_good_at: datetime | None = None
        self.last_published: float | None = None
        self.last_seen: float | None = None
        self.last_seen_at: datetime | None = None
        self.last_change_at: datetime | None = None
        self.gap_started_at: datetime | None = None
        self.zero_since: datetime | None = None
        self.zero_hits = 0
        self.recovery_hold_remaining = 0
        self.typical_increase: float | None = None
        self.invalid_reported = False

    # -- helpers ---------------------------------------------------------
    def _output(self, value: float) -> float:
        """Apply offset/scale and rounding to a source value."""
        definition = self.definition
        offset = definition.offset or 0.0
        return round(max(0.0, value + offset), definition.precision)

    def _event(
        self,
        kind: str,
        severity: str,
        now: datetime,
        *,
        message: str,
        previous: float | None = None,
        invalid: float | None = None,
        estimated: float | None = None,
        extra: dict | None = None,
    ) -> AnomalyEvent:
        """Build an anomaly event for the diagnostic log."""
        return AnomalyEvent(
            kind=kind,
            severity=severity,
            timestamp=now,
            source_entity=self.definition.source_entity_id,
            previous_value=previous,
            invalid_value=invalid,
            restored_value=None,
            estimated_false_energy=estimated,
            unit=self.definition.unit_of_measurement,
            message=message,
            details=extra or {},
        )

    def _hold(self, reason: str, now: datetime, raw: float | None) -> GuardOutcome:
        """Return a holding outcome."""
        return GuardOutcome(
            publish=False,
            available=False,
            value=None,
            raw_value=raw,
            reason=reason,
            last_good=self.last_good,
            held_since=now,
        )

    # -- main entry point -----------------------------------------------
    def evaluate(self, raw_state: str | None, now: datetime) -> GuardOutcome:
        """Evaluate a raw source state string (``hass.states`` value)."""
        return self.evaluate_value(parse_float(raw_state), now, raw_repr=raw_state)

    def evaluate_value(
        self,
        raw: float | None,
        now: datetime,
        raw_repr: str | None = None,
    ) -> GuardOutcome:
        """Evaluate a numeric value and decide what to publish.

        ``raw`` is ``None`` when the source is unusable (unavailable, unknown,
        non numeric, or a derived sensor whose sources cannot be read).
        """
        definition = self.definition

        if raw is None or not math.isfinite(raw):
            # Non-finite values never reach the comparisons below: NaN would
            # make every check fail and publish the NaN as a valid reading.
            return self._evaluate_invalid(
                raw_repr if raw_repr is not None else "unavailable", now
            )

        # Remember whether we are recovering from a gap.
        recovered_after = None
        if self.gap_started_at is not None:
            recovered_after = now - self.gap_started_at
            self.gap_started_at = None
            grace = timedelta(seconds=definition.grace_period)
            if recovered_after >= grace:
                self.recovery_hold_remaining = max(
                    0, definition.recovery_hold_scans - 1
                )

        # Normalise into the protected unit.
        value = raw * self._scale_factor()

        event: AnomalyEvent | None = None
        if self.last_good is None:
            # First ever reading: trust it, that is the only sane baseline.
            self._accept(value, now)
            if recovered_after is not None:
                event = self._event(
                    EVENT_SOURCE_RECOVERED,
                    SEVERITY_INFO,
                    now,
                    message=(
                        "Source recovered after "
                        f"{recovered_after.total_seconds():.0f}s and reported "
                        f"{value}"
                    ),
                    previous=None,
                    invalid=value,
                    extra={"gap_seconds": recovered_after.total_seconds()},
                )
            return GuardOutcome(
                publish=True,
                available=True,
                value=self._output(value),
                raw_value=value,
                reason=REASON_FIRST_VALUE,
                last_good=self.last_good,
                event=event,
            )

        # 1. Implausible drop (the reconnect signature).
        if value < self.last_good - self._epsilon():
            outcome = self._handle_drop(value, now)
            if outcome is not None:
                return outcome

        # 2. Reconnect stabilisation window.
        if self.recovery_hold_remaining > 0:
            self.recovery_hold_remaining -= 1
            self.last_seen = value
            self.last_seen_at = now
            return self._hold(REASON_RECOVERY_HOLD, now, value)

        # 3. Implausible jump upwards.
        if self.last_good is not None and value > self.last_good:
            increase = value - self.last_good
            threshold, expected = self._jump_threshold()
            if increase > threshold:
                event = self._event(
                    EVENT_LARGE_JUMP,
                    SEVERITY_WARNING,
                    now,
                    message=(
                        f"Source jumped by {increase:.3f} "
                        f"{definition.unit_of_measurement} in one update "
                        f"(previous {self.last_good}, new {value})"
                    ),
                    previous=self.last_good,
                    invalid=value,
                    estimated=max(0.0, increase - expected),
                    extra={
                        "increase": increase,
                        "expected_increase": expected,
                        "threshold": threshold,
                    },
                )
                if definition.reject_large_jumps:
                    return GuardOutcome(
                        publish=False,
                        available=False,
                        value=None,
                        raw_value=value,
                        reason=REASON_JUMP_HELD,
                        last_good=self.last_good,
                        held_since=now,
                        event=event,
                    )
            self._update_typical_increase(increase)

        # 4. Hard upper limit.
        if definition.max_value is not None and value > definition.max_value:
            return GuardOutcome(
                publish=False,
                available=False,
                value=None,
                raw_value=value,
                reason=REASON_MAX_HELD,
                last_good=self.last_good,
                held_since=now,
                event=self._event(
                    EVENT_ABOVE_MAX,
                    SEVERITY_WARNING,
                    now,
                    message=(
                        f"Source reported {value} which is above the configured "
                        f"maximum of {definition.max_value}"
                    ),
                    previous=self.last_good,
                    invalid=value,
                ),
            )

        self._accept(value, now)
        if recovered_after is not None:
            event = self._event(
                EVENT_SOURCE_RECOVERED,
                SEVERITY_INFO,
                now,
                message=(
                    f"Source recovered after {recovered_after.total_seconds():.0f}s "
                    f"with value {value}"
                ),
                previous=self.last_published,
                invalid=value,
                extra={"gap_seconds": recovered_after.total_seconds()},
            )
        return GuardOutcome(
            publish=True,
            available=True,
            value=self._output(value),
            raw_value=value,
            reason=REASON_OK,
            last_good=self.last_good,
            event=event,
        )

    # -- internals -------------------------------------------------------
    def _evaluate_invalid(self, raw_state: str | None, now: datetime) -> GuardOutcome:
        """Handle ``unavailable`` / ``unknown`` / non numeric source states."""
        if self.gap_started_at is None:
            self.gap_started_at = now
        self.zero_hits = 0
        self.zero_since = None

        event = None
        if not self.invalid_reported and self.last_good is not None:
            self.invalid_reported = True
            event = self._event(
                EVENT_SOURCE_INVALID,
                SEVERITY_INFO,
                now,
                message=(
                    f"Source reported '{raw_state}' while the last good value was "
                    f"{self.last_good}; holding the last good value"
                ),
                previous=self.last_good,
                invalid=None,
                extra={"raw_state": raw_state},
            )
        return GuardOutcome(
            publish=False,
            available=False,
            value=None,
            raw_value=None,
            reason=REASON_INVALID_SOURCE,
            last_good=self.last_good,
            held_since=self.gap_started_at,
            event=event,
        )

    def _handle_drop(self, value: float, now: datetime) -> GuardOutcome | None:
        """Handle a reading below the last good value.

        Returns ``None`` when the drop is accepted (guard disabled by the user).
        """
        definition = self.definition
        assert self.last_good is not None
        previous = self.last_good
        is_zero = value <= self._epsilon()

        # Tiny backwards corrections (rounding noise) are never interesting.
        if value >= previous - max(self._epsilon(), previous * 0.005):
            self._accept(value, now)
            return GuardOutcome(
                publish=True,
                available=True,
                value=self._output(value),
                raw_value=value,
                reason=REASON_OK,
                last_good=self.last_good,
            )

        # A zero that follows another zero is not a reconnect artefact.
        if is_zero and previous < definition.zero_min_previous:
            self._accept(value, now)
            return GuardOutcome(
                publish=True,
                available=True,
                value=self._output(value),
                raw_value=value,
                reason=REASON_OK,
                last_good=self.last_good,
            )

        if not definition.do_not_decrease:
            # The user explicitly disabled the guard: accept the drop as a reset.
            self._accept(value, now, reset_typical=True)
            return GuardOutcome(
                publish=True,
                available=True,
                value=self._output(value),
                raw_value=value,
                reason=REASON_OK,
                last_good=self.last_good,
            )

        if definition.accept_real_reset:
            # Only accept a reset the user opted into, and only when confirmed:
            # a single zero during a reconnect must never be accepted.
            if is_zero:
                if self.zero_since is None:
                    self.zero_since = now
                self.zero_hits += 1
                if self.zero_hits >= max(1, definition.confirm_scans):
                    self._accept(value, now, reset_typical=True)
                    return GuardOutcome(
                        publish=True,
                        available=True,
                        value=self._output(value),
                        raw_value=value,
                        reason=REASON_RESET_ACCEPTED,
                        last_good=self.last_good,
                        event=self._event(
                            EVENT_RESET_ACCEPTED,
                            SEVERITY_INFO,
                            now,
                            message=(
                                f"Confirmed meter reset accepted: {previous} -> "
                                f"{value} ({self.zero_hits} consecutive readings)"
                            ),
                            previous=previous,
                            invalid=value,
                            estimated=max(0.0, previous - value),
                            extra={"confirm_scans": self.zero_hits},
                        ),
                    )
            else:
                self._accept(value, now, reset_typical=True)
                return GuardOutcome(
                    publish=True,
                    available=True,
                    value=self._output(value),
                    raw_value=value,
                    reason=REASON_RESET_ACCEPTED,
                    last_good=self.last_good,
                    event=self._event(
                        EVENT_RESET_ACCEPTED,
                        SEVERITY_INFO,
                        now,
                        message=(
                            f"Reset accepted (accept_real_reset enabled): "
                            f"{previous} -> {value}"
                        ),
                        previous=previous,
                        invalid=value,
                    ),
                )

        # Blocked drop: this is the case that corrupts the Energy Dashboard.
        # If the recorder accepted the low value it would restart counting from
        # it and add ``previous - value`` kWh of phantom energy on the way back.
        false_energy = max(0.0, previous - value)
        if is_zero:
            return GuardOutcome(
                publish=False,
                available=False,
                value=None,
                raw_value=value,
                reason=REASON_ZERO_HELD,
                last_good=previous,
                held_since=self.zero_since or now,
                event=self._event(
                    EVENT_ZERO_RESET_BLOCKED,
                    SEVERITY_WARNING,
                    now,
                    message=(
                        f"Blocked a drop to {value} from {previous} "
                        f"{definition.unit_of_measurement} (possible reconnect) - "
                        "publishing 'unavailable' instead of a false zero"
                    ),
                    previous=previous,
                    invalid=value,
                    estimated=false_energy,
                ),
            )
        return GuardOutcome(
            publish=False,
            available=False,
            value=None,
            raw_value=value,
            reason=REASON_DECREASE_HELD,
            last_good=previous,
            held_since=self.zero_since or now,
            event=self._event(
                EVENT_DECREASE_BLOCKED,
                SEVERITY_WARNING,
                now,
                message=(
                    f"Blocked a decrease from {previous} to {value} "
                    f"{definition.unit_of_measurement} "
                    "(do-not-decrease guard enabled)"
                ),
                previous=previous,
                invalid=value,
                estimated=false_energy,
            ),
        )

    def _accept(
        self, value: float, now: datetime, *, reset_typical: bool = False
    ) -> None:
        """Accept ``value`` as the new last good reading."""
        if self.last_good is not None and value != self.last_good:
            self.last_change_at = now
        self.last_good = value
        self.last_good_at = now
        self.last_published = value
        self.last_seen = value
        self.last_seen_at = now
        self.zero_hits = 0
        self.zero_since = None
        self.invalid_reported = False
        if reset_typical:
            self.typical_increase = None

    def _update_typical_increase(self, increase: float) -> None:
        """Track an exponential moving average of observed increases."""
        if increase <= 0:
            return
        if self.typical_increase is None:
            self.typical_increase = increase
        else:
            self.typical_increase = (
                _EMA_ALPHA * increase + (1 - _EMA_ALPHA) * self.typical_increase
            )

    def _jump_threshold(self) -> tuple[float, float]:
        """Return the jump threshold and the expected increase."""
        definition = self.definition
        expected = self.typical_increase or 0.0
        threshold = definition.large_jump
        if expected > 0:
            threshold = max(threshold, expected * definition.large_jump_ratio)
        return threshold, expected

    def _scale_factor(self) -> float:
        """Return the factor converting source units into the target unit."""
        from .units import unit_factor  # local import to avoid cycles

        definition = self.definition
        return unit_factor(definition.source_unit, definition.unit_of_measurement)

    def _epsilon(self) -> float:
        """Return the tolerance used to detect decreases."""
        return max(0.0, 10 ** (-self.definition.precision))

    # -- restore support --------------------------------------------------
    def seed(self, value: float | None, at: datetime | None = None) -> None:
        """Seed the guard with a restored value (after a Home Assistant restart)."""
        number = parse_float(value)
        if number is None:
            return
        self.last_good = number
        self.last_published = number
        self.last_good_at = at
        self.last_seen = number
        self.last_seen_at = at
