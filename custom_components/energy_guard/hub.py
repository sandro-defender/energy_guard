"""Runtime hub for a single Energy Guard config entry.

The hub owns:

* the parsed configuration (definitions, detection rules, cost repair, backups),
* the diagnostic log (in memory + persisted in ``.storage``),
* the repair / calibration history,
* the backup and report folders,
* the coordinator that scans recorder statistics.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    BACKUPS_DIR,
    CONF_BACKUPS,
    CONF_COST,
    CONF_DERIVED,
    CONF_DETECTION,
    CONF_PROTECTED,
    CONF_UTILITY_METERS,
    DATA_HUBS,
    DOMAIN,
    EVENT_SIMULTANEOUS,
    MANUFACTURER,
    MAX_EVENTS_STORED,
    MODEL,
    NAME,
    REPORTS_DIR,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    STORAGE_VERSION,
    VERSION,
)
from .migrations import normalize_options
from .models import (
    AnomalyEvent,
    BackupConfig,
    CostRepairConfig,
    DetectionRules,
    EnergyGuardConfig,
    SensorDefinition,
    UtilityMeterDefinition,
)
from .units import is_energy_unit

_LOGGER = logging.getLogger(__name__)

DOCUMENTATION_URL = "https://github.com/Energy-Guard/energy-guard"

# Event kinds that indicate the source data itself is broken.  Used to detect
# "several sensors failed at the same time" situations (grid outages,
# inverter restarts, ...).
FAILURE_KINDS = frozenset(
    {"zero_reset_blocked", "decrease_blocked", "large_jump", "value_above_max"}
)


class EnergyGuardHub:
    """Holds all runtime state of one Energy Guard config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the hub for a config entry."""
        self.hass = hass
        self.entry = entry
        # Stored options are always normalized on load: a sparse or damaged
        # payload falls back to the documented defaults instead of failing.
        options, notes = normalize_options(dict(entry.options))
        for note in notes:
            _LOGGER.warning("Energy Guard configuration: %s", note)
        self.config = EnergyGuardConfig(
            protected=[
                SensorDefinition.from_dict(item) for item in options[CONF_PROTECTED]
            ],
            derived=[
                SensorDefinition.from_dict(item) for item in options[CONF_DERIVED]
            ],
            utility_meters=[
                UtilityMeterDefinition.from_dict(item)
                for item in options[CONF_UTILITY_METERS]
            ],
            detection=DetectionRules.from_dict(options[CONF_DETECTION]),
            cost=CostRepairConfig.from_dict(options[CONF_COST]),
            backups=BackupConfig.from_dict(options[CONF_BACKUPS]),
        )
        self.coordinator: Any = None  # set in async_setup_entry
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.events: deque[AnomalyEvent] = deque(maxlen=MAX_EVENTS_STORED)
        self.repairs: deque[dict[str, Any]] = deque(maxlen=100)
        self.calibrations: deque[dict[str, Any]] = deque(maxlen=100)
        self.scan_candidates: list[dict[str, Any]] = []
        self.reported_fingerprints: set[str] = set()
        self.last_scan: datetime | None = None
        self.last_scan_error: str | None = None
        self.last_scan_scope: str | None = None
        self.last_scan_statistic_count: int = 0
        self._listeners: set[Callable[[], None]] = set()
        self._entities: dict[str, str] = {}

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def async_load(self) -> None:
        """Load the persisted diagnostic log."""
        data = await self._store.async_load() or {}
        for raw in data.get("events", []):
            try:
                self.events.append(AnomalyEvent.from_dict(raw))
            except (TypeError, ValueError):  # pragma: no cover - corrupt storage
                _LOGGER.debug("Skipping unreadable Energy Guard event: %s", raw)
        self.repairs.extend(data.get("repairs", []))
        self.calibrations.extend(data.get("calibrations", []))
        self.reported_fingerprints.update(data.get("reported_fingerprints", []))
        if len(self.reported_fingerprints) > 500:
            self.reported_fingerprints = set(list(self.reported_fingerprints)[-250:])
        self._prune_events()

    @callback
    def async_schedule_save(self) -> None:
        """Persist the diagnostic log (debounced)."""
        self._store.async_delay_save(self._data_to_save, 30)

    def _data_to_save(self) -> dict[str, Any]:
        """Return the data that is written to .storage."""
        return {
            "events": [event.to_dict() for event in self.events],
            "repairs": list(self.repairs),
            "calibrations": list(self.calibrations),
            "reported_fingerprints": sorted(self.reported_fingerprints),
        }

    async def async_save(self) -> None:
        """Persist the diagnostic log immediately."""
        await self._store.async_save(self._data_to_save())

    # ------------------------------------------------------------------
    # description helpers
    # ------------------------------------------------------------------
    @property
    def device_info(self) -> DeviceInfo:
        """Return the device all Energy Guard entities belong to."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=NAME,
            manufacturer=MANUFACTURER,
            model=MODEL,
            entry_type=DeviceEntryType.SERVICE,
            sw_version=VERSION,
            configuration_url=DOCUMENTATION_URL,
        )

    @property
    def base_dir(self) -> Path:
        """Return the Energy Guard directory inside the configuration folder."""
        return Path(self.hass.config.path(DOMAIN))

    @property
    def backup_dir(self) -> Path:
        """Return the directory used for statistics backups."""
        return self.base_dir / BACKUPS_DIR

    @property
    def report_dir(self) -> Path:
        """Return the directory used for exported reports."""
        return self.base_dir / REPORTS_DIR

    # ------------------------------------------------------------------
    # entity registry bookkeeping
    # ------------------------------------------------------------------
    @callback
    def register_entity(self, definition_id: str, entity_id: str | None) -> None:
        """Remember the entity id of a definition (used in logs and reports)."""
        if entity_id:
            self._entities[definition_id] = entity_id

    @callback
    def entity_id_for(self, definition_id: str) -> str | None:
        """Return the entity id of a definition."""
        return self._entities.get(definition_id)

    # ------------------------------------------------------------------
    # listeners
    # ------------------------------------------------------------------
    @callback
    def async_add_listener(
        self, update_callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Listen for changes of the live diagnostic state."""
        self._listeners.add(update_callback)

        def remove_listener() -> None:
            self._listeners.discard(update_callback)

        return remove_listener

    @callback
    def async_update_listeners(self) -> None:
        """Notify all listeners."""
        for update_callback in list(self._listeners):
            update_callback()

    # ------------------------------------------------------------------
    # diagnostic log
    # ------------------------------------------------------------------
    @callback
    def log_event(self, event: AnomalyEvent) -> AnomalyEvent:
        """Add an event to the diagnostic log."""
        self._prune_events()
        self.events.append(event)
        self._detect_simultaneous(event)
        self.async_schedule_save()
        self.async_update_listeners()
        if event.severity in (SEVERITY_WARNING, SEVERITY_ERROR):
            _LOGGER.warning("Energy Guard: %s", event.message)
        else:
            _LOGGER.debug("Energy Guard: %s", event.message)
        return event

    def _prune_events(self) -> None:
        """Drop events older than the configured retention."""
        retention = timedelta(days=self.config.detection.event_retention_days)
        cutoff = dt_util.utcnow() - retention
        while self.events and self.events[0].timestamp < cutoff:
            self.events.popleft()

    def _detect_simultaneous(self, event: AnomalyEvent) -> None:
        """Detect several energy sensors failing at roughly the same time."""
        if event.kind not in FAILURE_KINDS:
            return
        window = timedelta(minutes=self.config.detection.simultaneous_window_minutes)
        since = event.timestamp - window
        affected = {
            item.source_entity or item.protected_entity
            for item in self.events
            if item.kind in FAILURE_KINDS and item.timestamp >= since
        }
        affected.discard(None)
        threshold = max(2, self.config.detection.simultaneous_threshold)
        if len(affected) < threshold:
            return
        already_reported = any(
            item.kind == EVENT_SIMULTANEOUS
            and item.timestamp >= since
            and set(item.details.get("affected_entities", [])) >= affected
            for item in self.events
        )
        if already_reported:
            return
        self.events.append(
            AnomalyEvent(
                kind=EVENT_SIMULTANEOUS,
                severity=SEVERITY_ERROR,
                timestamp=event.timestamp,
                source_entity=None,
                protected_entity=None,
                unit=event.unit,
                message=(
                    f"{len(affected)} energy sensors reported anomalies within "
                    f"{self.config.detection.simultaneous_window_minutes} minutes: "
                    + ", ".join(sorted(affected))
                ),
                details={
                    "affected_entities": sorted(affected),
                    "window_minutes": self.config.detection.simultaneous_window_minutes,
                },
            )
        )

    @property
    def last_event(self) -> AnomalyEvent | None:
        """Return the most recent event."""
        return self.events[-1] if self.events else None

    def events_since(self, since: datetime) -> list[AnomalyEvent]:
        """Return all events newer than ``since``."""
        return [event for event in self.events if event.timestamp >= since]

    @property
    def issue_window_start(self) -> datetime:
        """Return the start of the window used by the issue binary sensor."""
        return dt_util.utcnow() - timedelta(
            hours=self.config.detection.issue_window_hours
        )

    def has_open_issues(self) -> bool:
        """Return True when a problem should be surfaced to the user."""
        if self.scan_candidates:
            return True
        return any(
            event.severity in (SEVERITY_WARNING, SEVERITY_ERROR)
            for event in self.events_since(self.issue_window_start)
        )

    @property
    def estimated_false_energy(self) -> float:
        """Return the sum of the estimated false energy of the recent events."""
        total = 0.0
        for event in self.events_since(self.issue_window_start):
            if event.estimated_false_energy:
                total += event.estimated_false_energy
        return round(total, 3)

    @property
    def anomaly_count(self) -> int:
        """Return the number of warning/error events inside the issue window."""
        return sum(
            1
            for event in self.events_since(self.issue_window_start)
            if event.severity in (SEVERITY_WARNING, SEVERITY_ERROR)
        )

    # ------------------------------------------------------------------
    # history helpers
    # ------------------------------------------------------------------
    @callback
    def record_repair(self, record: dict[str, Any]) -> None:
        """Store a repair record."""
        self.repairs.append(record)
        self.async_schedule_save()
        self.async_update_listeners()

    @callback
    def record_calibration(self, record: dict[str, Any]) -> None:
        """Store a utility meter calibration record."""
        self.calibrations.append(record)
        self.async_schedule_save()
        self.async_update_listeners()

    # ------------------------------------------------------------------
    # configuration helpers
    # ------------------------------------------------------------------
    def detection_for(self, definition: SensorDefinition) -> SensorDefinition:
        """Return a definition with the global detection defaults applied."""
        return definition

    def find_definition(self, entity_id: str) -> SensorDefinition | None:
        """Return the definition that produces ``entity_id``."""
        for definition in self.config.all_definitions:
            if self.entity_id_for(definition.id) == entity_id:
                return definition
        return None

    def statistic_ids(self) -> list[str]:
        """Return the statistic ids that Energy Guard should supervise."""
        statistic_ids: list[str] = []
        for definition in self.config.all_definitions:
            entity_id = self.entity_id_for(definition.id)
            if entity_id:
                statistic_ids.append(entity_id)
        statistic_ids.extend(
            source
            for definition in self.config.all_definitions
            for source in definition.all_sources
            if source.startswith("sensor.")
        )
        if self.config.cost.cost_statistic_id:
            statistic_ids.append(self.config.cost.cost_statistic_id)
        if self.config.cost.energy_statistic_id:
            statistic_ids.append(self.config.cost.energy_statistic_id)
        return list(dict.fromkeys(statistic_ids))

    def source_unit(self, entity_id: str) -> str | None:
        """Return the unit of a source entity."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        unit = state.attributes.get("unit_of_measurement")
        return unit if isinstance(unit, str) else None

    def validate_energy_sources(self, entity_ids: list[str]) -> list[str]:
        """Return the sources that do not look like energy sensors."""
        problems: list[str] = []
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            if state is None:
                problems.append(f"{entity_id} (entity not found)")
                continue
            unit = self.source_unit(entity_id)
            if unit and not is_energy_unit(unit):
                problems.append(f"{entity_id} (unit {unit} is not an energy unit)")
            state_class = state.attributes.get("state_class")
            if state_class not in ("total_increasing", "total"):
                problems.append(
                    f"{entity_id} (state_class is {state_class!r}, expected "
                    "'total_increasing')"
                )
        return problems


def get_hubs(hass: HomeAssistant) -> dict[str, EnergyGuardHub]:
    """Return all hubs of the Energy Guard integration."""
    return hass.data.get(DOMAIN, {}).get(DATA_HUBS, {})


def get_hub(hass: HomeAssistant, entry_id: str | None = None) -> EnergyGuardHub | None:
    """Return one hub, or the single configured hub."""
    hubs = get_hubs(hass)
    if entry_id:
        return hubs.get(entry_id)
    if len(hubs) == 1:
        return next(iter(hubs.values()))
    return None


def resolve_hubs(
    hass: HomeAssistant, entry_id: str | None = None
) -> list[EnergyGuardHub]:
    """Return the hubs a service call should act on."""
    hubs = get_hubs(hass)
    if entry_id:
        hub = hubs.get(entry_id)
        return [hub] if hub else []
    return list(hubs.values())
