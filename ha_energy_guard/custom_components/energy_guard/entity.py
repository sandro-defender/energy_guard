"""Entity base classes for Energy Guard.

Naming note: Energy Guard deliberately does **not** use ``has_entity_name``.
Protected sensors are meant to be drop-in replacements for their source entity
(``sensor.grid_import`` -> ``sensor.grid_import_protected``), so the full,
user-chosen name is used as the entity id.  The diagnostic entities are named
``Energy Guard ...`` which yields the documented entity ids
``sensor.energy_guard_last_anomaly`` and ``binary_sensor.energy_guard_data_issue``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.core import Event, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, REASON_OK, RECOMMENDED_ACTIONS
from .hub import EnergyGuardHub
from .models import AnomalyEvent, SensorDefinition
from .protect import SensorGuard, parse_float
from .units import unit_factor

_LOGGER = logging.getLogger(__name__)

# How often a held value is re-checked so that ``confirm_scans`` also works
# while the source is not changing its state (for example a stuck ``0``).
RECHECK_INTERVAL = timedelta(seconds=30)


class EnergyGuardDeviceMixin:
    """Mixin providing the shared Energy Guard device and event annotation."""

    hub: EnergyGuardHub

    @callback
    def _annotate_event(self, event: AnomalyEvent, reason: str) -> None:
        """Add the guard status and the recommended action to a log entry.

        Every event answers "what happened" *and* "what should I do now", so a
        user - or a maintainer reading an issue report - does not have to
        reverse engineer the reason codes.
        """
        event.details.setdefault("guard_status", getattr(self, "_guard_status", None))
        event.details.setdefault("reason", reason)
        event.details.setdefault(
            "source_state", getattr(self, "_last_source_state", None)
        )
        event.details.setdefault(
            "recommended_action",
            RECOMMENDED_ACTIONS.get(
                event.kind,
                "Review the Energy Guard diagnostic log and the statistics scan.",
            ),
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device of this entity."""
        return self.hub.device_info


class EnergyGuardProtectedSensor(EnergyGuardDeviceMixin, RestoreSensor):
    """A cumulative energy sensor that cannot publish reconnect artefacts."""

    # A protected sensor always stays a cumulative energy sensor so that it can
    # be used as a drop-in replacement in the Energy Dashboard.
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, hub: EnergyGuardHub, definition: SensorDefinition) -> None:
        """Initialise the protected sensor."""
        self.hub = hub
        self.definition = definition
        self.guard = SensorGuard(definition)
        self._attr_unique_id = f"{DOMAIN}_{definition.id}"
        self._attr_name = definition.name
        self._attr_native_unit_of_measurement = definition.unit_of_measurement
        self._attr_available = False
        self._attr_native_value = None
        self._last_source_state: str | None = None
        self._last_event_kind: str | None = None
        self._last_reason_code = "unknown"
        self._recheck_unsub = None
        self._source_unit = definition.source_unit
        self.entity_id = f"sensor.{_slug(definition.name)}"

    # ------------------------------------------------------------------
    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return diagnostic attributes for dashboards."""
        guard = self.guard
        return {
            "source_entity_id": self.definition.source_entity_id,
            "source_state": self._last_source_state,
            "guard_status": self._guard_status,
            "holds_value": self._attr_available is False
            and guard.last_good is not None,
            "last_good_value": guard.last_good,
            "last_good_at": guard.last_good_at.isoformat()
            if guard.last_good_at
            else None,
            "holding_since": (
                guard.gap_started_at.isoformat() if guard.gap_started_at else None
            ),
            "typical_increase": (
                round(guard.typical_increase, 3)
                if guard.typical_increase is not None
                else None
            ),
            "offset": self.definition.offset,
            "do_not_decrease": self.definition.do_not_decrease,
            "accept_real_reset": self.definition.accept_real_reset,
            "confirm_scans": self.definition.confirm_scans,
            "last_blocked_event": self._last_event_kind,
            "estimated_false_energy": self.hub.estimated_false_energy,
            "unit_of_measurement": self.definition.unit_of_measurement,
        }

    # ------------------------------------------------------------------
    async def async_added_to_hass(self) -> None:
        """Restore the last good value and subscribe to the source."""
        await super().async_added_to_hass()
        self.hub.register_entity(self.definition.id, self.entity_id)

        last = await self.async_get_last_sensor_data()
        if last is not None:
            restored = parse_float(last.native_value)
            if restored is not None:
                self.guard.seed(restored, dt_util.utcnow())
                self._attr_native_value = restored
                self._attr_available = True
                _LOGGER.debug(
                    "Energy Guard restored %s to %s", self.entity_id, restored
                )

        source = self.definition.source_entity_id
        if source:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [source], self._handle_source_event
                )
            )
            self._source_unit = self.hub.source_unit(source) or self._source_unit
            self.definition.source_unit = self._source_unit
            self._async_evaluate(self.hass.states.get(source))

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the re-check timer."""
        self._async_cancel_recheck()

    # ------------------------------------------------------------------
    @callback
    def _handle_source_event(self, event: Event) -> None:
        """Handle a state change of the source entity."""
        self._async_evaluate(event.data.get("new_state"))

    @callback
    def _async_evaluate(self, source_state: object) -> None:
        """Run the protection engine for the current source state."""
        definition = self.definition
        raw_state = getattr(source_state, "state", None)
        if source_state is not None:
            unit = getattr(source_state, "attributes", {}).get("unit_of_measurement")
            if isinstance(unit, str):
                self._source_unit = unit
                self.definition.source_unit = unit
        self._last_source_state = raw_state

        outcome = self.guard.evaluate(raw_state, dt_util.utcnow())
        self._last_reason_code = outcome.reason

        if outcome.publish and outcome.value is not None:
            self._attr_native_value = outcome.value
            self._attr_available = True
            self._async_cancel_recheck()
        else:
            # Publishing "unavailable" is exactly the point: the recorder stores
            # an unavailable state, which never adds energy to the statistics.
            self._attr_available = False
            self._attr_native_value = None
            self._async_schedule_recheck()

        if outcome.event is not None:
            event = outcome.event
            event.protected_entity = self.entity_id
            event.source_entity = definition.source_entity_id
            self._annotate_event(event, outcome.reason)
            self._last_event_kind = (
                outcome.event.kind
                if outcome.reason != REASON_OK
                else self._last_event_kind
            )
            self.hub.log_event(event)

        self.async_write_ha_state()

    @property
    def _guard_status(self) -> str:
        """Return the last decision of the protection engine."""
        return self._last_reason_code

    @callback
    def _async_schedule_recheck(self) -> None:
        """Re-check a held value periodically (zero confirmation, grace period)."""
        if self._recheck_unsub is not None:
            return

        async def _recheck(_now: datetime) -> None:
            self._async_evaluate(self.hass.states.get(self.definition.source_entity_id))

        self._recheck_unsub = async_track_time_interval(
            self.hass, _recheck, RECHECK_INTERVAL
        )
        self.async_on_remove(self._recheck_unsub)

    @callback
    def _async_cancel_recheck(self) -> None:
        """Stop the periodic re-check."""
        if self._recheck_unsub is not None:
            self._recheck_unsub()
            self._recheck_unsub = None

    # ------------------------------------------------------------------


class EnergyGuardDerivedSensor(EnergyGuardDeviceMixin, RestoreSensor):
    """A protected sensor that is derived from several sources."""

    # A protected sensor always stays a cumulative energy sensor so that it can
    # be used as a drop-in replacement in the Energy Dashboard.
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, hub: EnergyGuardHub, definition: SensorDefinition) -> None:
        """Initialise the derived sensor."""
        self.hub = hub
        self.definition = definition
        self.guard = SensorGuard(definition)
        self._attr_unique_id = f"{DOMAIN}_{definition.id}"
        self._attr_name = definition.name
        self._attr_native_unit_of_measurement = definition.unit_of_measurement
        self._attr_available = False
        self._attr_native_value = None
        self._last_source_state: str | None = None
        self._last_event_kind: str | None = None
        self._last_reason_code = "unknown"
        self._recheck_unsub = None
        self._sources = definition.all_sources
        self.entity_id = f"sensor.{_slug(definition.name)}"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return diagnostic attributes for dashboards."""
        return {
            "mode": self.definition.mode,
            "sources": self._sources,
            "source_values": self._read_sources()[1],
            "guard_status": self._last_reason_code,
            "last_good_value": self.guard.last_good,
            "do_not_decrease": self.definition.do_not_decrease,
            "scale": self.definition.scale,
            "offset": self.definition.offset,
            "estimated_false_energy": self.hub.estimated_false_energy,
        }

    async def async_added_to_hass(self) -> None:
        """Restore the last value and subscribe to all sources."""
        await super().async_added_to_hass()
        self.hub.register_entity(self.definition.id, self.entity_id)

        last = await self.async_get_last_sensor_data()
        if last is not None:
            restored = parse_float(last.native_value)
            if restored is not None:
                self.guard.seed(restored, dt_util.utcnow())
                self._attr_native_value = restored
                self._attr_available = True

        if self._sources:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, list(self._sources), self._handle_source_event
                )
            )
            self._async_evaluate(None)

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the re-check timer."""
        self._async_cancel_recheck()

    # ------------------------------------------------------------------
    @callback
    def _handle_source_event(self, _event: Event) -> None:
        """Handle a state change of any source."""
        self._async_evaluate(None)

    def _read_sources(self) -> tuple[float | None, dict[str, float | None]]:
        """Read and normalise all sources into the target unit."""
        definition = self.definition
        values: dict[str, float | None] = {}
        usable: list[float] = []
        for entity_id in self._sources:
            state = self.hass.states.get(entity_id)
            number = parse_float(getattr(state, "state", None))
            values[entity_id] = number
            if number is None:
                continue
            unit = getattr(state, "attributes", {}).get("unit_of_measurement")
            factor = (
                unit_factor(unit, definition.unit_of_measurement)
                if isinstance(unit, str)
                else 1.0
            )
            usable.append(number * factor * definition.scale)
        if not usable:
            return None, values

        if definition.mode == "sum":
            computed = sum(usable)
        elif definition.mode == "difference" or definition.mode == "phase_split":
            computed = usable[0] - sum(usable[1:])
        else:  # pragma: no cover - guarded by the flow schema
            computed = sum(usable)
        return computed, values

    @callback
    def _async_evaluate(self, _source_state: object) -> None:
        """Compute the derived value and run the protection engine."""
        definition = self.definition
        computed, values = self._read_sources()
        self._last_source_state = (
            ", ".join(f"{entity}={value}" for entity, value in values.items()) or None
        )

        missing = [entity for entity, value in values.items() if value is None]
        if computed is not None and missing and definition.require_all_sources:
            # Publishing a partial sum would look like a drop; hold instead.
            computed = None

        outcome = self.guard.evaluate_value(
            computed, dt_util.utcnow(), raw_repr=self._last_source_state
        )
        self._last_reason_code = outcome.reason

        if outcome.publish and outcome.value is not None:
            self._attr_native_value = outcome.value
            self._attr_available = True
            self._async_cancel_recheck()
        else:
            self._attr_available = False
            self._async_schedule_recheck()

        if outcome.event is not None:
            event = outcome.event
            event.protected_entity = self.entity_id
            event.source_entity = ", ".join(self._sources)
            self._annotate_event(event, outcome.reason)
            if outcome.reason != REASON_OK:
                self._last_event_kind = event.kind
            self.hub.log_event(event)

        self.async_write_ha_state()

    @property
    def _guard_status(self) -> str:
        """Return the last decision of the protection engine."""
        return self._last_reason_code

    @callback
    def _async_schedule_recheck(self) -> None:
        """Re-check held values periodically."""
        if self._recheck_unsub is not None:
            return

        async def _recheck(_now: datetime) -> None:
            self._async_evaluate(None)

        self._recheck_unsub = async_track_time_interval(
            self.hass, _recheck, RECHECK_INTERVAL
        )
        self.async_on_remove(self._recheck_unsub)

    @callback
    def _async_cancel_recheck(self) -> None:
        """Stop the periodic re-check."""
        if self._recheck_unsub is not None:
            self._recheck_unsub()
            self._recheck_unsub = None


class EnergyGuardDiagnosticEntity(EnergyGuardDeviceMixin, CoordinatorEntity):
    """Base class for the Energy Guard diagnostic entities."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, hub: EnergyGuardHub) -> None:
        """Initialise the diagnostic entity."""
        self.hub = hub
        super().__init__(hub.coordinator)
        self._attr_available = True

    async def async_added_to_hass(self) -> None:
        """Listen to live hub updates in addition to coordinator updates."""
        await super().async_added_to_hass()
        self.async_on_remove(self.hub.async_add_listener(self._handle_hub_update))

    @callback
    def _handle_hub_update(self) -> None:
        """Write the state when the diagnostic log changes."""
        self.async_write_ha_state()

    @property
    def scan_result(self):
        """Return the latest scan result, if any."""
        return self.coordinator.data


def _slug(value: str) -> str:
    """Return a Home Assistant object id for a name."""
    from homeassistant.util import slugify

    return slugify(value)
