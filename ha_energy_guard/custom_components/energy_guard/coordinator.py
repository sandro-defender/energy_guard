"""Background scanner: periodically look for corrupted statistics (read-only).

The coordinator is the "eyes" of the integration.  Every ``scan_interval`` it
reads the long term statistics of the protected sensors and their cost
statistics, stores the candidates on the hub (which feeds the diagnostic
entities) and raises a Repairs issue when something needs the user's attention.

It never changes data: repairing is always an explicit, confirmed service call
(see :mod:`custom_components.energy_guard.statistics`).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    EVENT_STATISTICS_OFFSET,
    NAME,
    RECOMMENDED_ACTIONS,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
)
from .costs import effective_price
from .detection import ScanResult, async_scan
from .models import AnomalyEvent, ScanCandidate
from .repairs import async_sync_repair_issues

_LOGGER = logging.getLogger(__name__)


class EnergyGuardCoordinator(DataUpdateCoordinator[ScanResult]):
    """Scan statistics in the background to find suspect offsets.

    The coordinator never modifies data.  It fills the dashboard diagnostic
    entities and adds an event to the diagnostic log whenever a *new* suspicious
    offset is found.
    """

    def __init__(self, hass: HomeAssistant, hub: Any) -> None:
        """Initialise the coordinator."""
        self.hub = hub
        super().__init__(
            hass,
            _LOGGER,
            name=f"{NAME} statistics scan",
            config_entry=hub.entry,
            update_interval=timedelta(
                seconds=max(60, hub.config.detection.scan_interval)
            ),
        )

    async def _async_update_data(self) -> ScanResult:
        """Scan the recent statistics history."""
        detection = self.hub.config.detection
        cost = self.hub.config.cost
        price = effective_price(self.hass, cost) if cost.enabled else None
        end = dt_util.utcnow()
        start = end - timedelta(hours=detection.lookback_hours)
        statistic_ids = self.hub.statistic_ids()

        try:
            result = await async_scan(
                self.hass,
                start=start,
                end=end,
                statistic_ids=statistic_ids,
                detection=detection,
                cost_energy_statistic_id=(
                    cost.energy_statistic_id if cost.enabled else None
                ),
                cost_statistic_id=cost.cost_statistic_id if cost.enabled else None,
                price=price,
                currency=cost.currency if cost.enabled else None,
            )
        except Exception as err:
            _LOGGER.debug("Energy Guard statistics scan failed: %s", err)
            self.hub.last_scan = dt_util.utcnow()
            self.hub.last_scan_error = str(err)
            self.hub.async_update_listeners()
            await async_sync_repair_issues(self.hass, self.hub)
            return ScanResult(
                start=start,
                end=end,
                statistic_ids=statistic_ids,
                warnings=[f"Statistics scan failed: {err}"],
                recorder_available=False,
            )

        self.hub.scan_candidates = [item.to_dict() for item in result.candidates]
        self.hub.last_scan = dt_util.utcnow()
        self.hub.last_scan_error = None
        self._log_new_candidates(result.candidates)
        self.hub.async_update_listeners()
        await async_sync_repair_issues(self.hass, self.hub)
        return result

    def _log_new_candidates(self, candidates: list[ScanCandidate]) -> None:
        """Add one diagnostic event per newly discovered suspicious offset."""
        for candidate in candidates:
            if candidate.fingerprint in self.hub.reported_fingerprints:
                continue
            self.hub.reported_fingerprints.add(candidate.fingerprint)
            self.hub.log_event(
                AnomalyEvent(
                    kind=EVENT_STATISTICS_OFFSET,
                    severity=(
                        SEVERITY_ERROR
                        if candidate.severity == "error"
                        else SEVERITY_WARNING
                    ),
                    timestamp=dt_util.utcnow(),
                    source_entity=candidate.statistic_id,
                    protected_entity=candidate.statistic_id,
                    invalid_value=candidate.observed_delta,
                    estimated_false_energy=candidate.estimated_false_energy,
                    unit=candidate.unit,
                    message=(
                        f"Statistics of {candidate.statistic_id} contain a suspicious "
                        f"increase of {candidate.observed_delta} {candidate.unit} at "
                        f"{candidate.start_time.isoformat()} "
                        f"(suggested repair offset {candidate.offset} "
                        f"{candidate.unit}). Run energy_guard.scan_statistics for "
                        "details; nothing is repaired automatically."
                    ),
                    details={
                        **candidate.to_dict(),
                        "guard_status": None,
                        "recommended_action": RECOMMENDED_ACTIONS[
                            EVENT_STATISTICS_OFFSET
                        ],
                    },
                )
            )
