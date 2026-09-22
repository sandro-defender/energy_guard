"""Low-level access to the Home Assistant recorder.

This module is the only place that talks to the recorder: it resolves the
recorder instance, reads long-term statistics, waits for the recorder queue and
compares statistic values before and after a change.

Only the official recorder APIs are used (``statistics_during_period``,
``async_block_till_done``) - Energy Guard never opens, reads or writes the
recorder database itself and never knows where that database lives.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Final

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import ALREADY_REPAIRED_TOLERANCE

_LOGGER = logging.getLogger(__name__)

#: How often a post-change verification re-reads a statistic before giving up.
VERIFY_ATTEMPTS: Final = 5
VERIFY_DELAY: Final = 0.2

PERIOD_HOUR = "hour"
STATISTIC_TYPES = {"sum", "state", "last_reset", "change"}


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
class RecorderUnavailableError(HomeAssistantError):
    """Raised when the recorder is not available for statistics operations."""

    def __init__(self) -> None:
        """Initialise the error."""
        super().__init__(
            "The Home Assistant recorder is not available. Energy Guard statistics "
            "repair needs an active recorder (the integration that stores the Energy "
            "Dashboard history). Enable 'recorder:' in configuration.yaml and restart "
            "Home Assistant, then try again."
        )


def recorder_instance(hass: HomeAssistant) -> Any:
    """Return the recorder instance, raising a helpful error if missing."""
    get_instance = None
    try:  # pragma: no cover - import location differs between versions
        from homeassistant.components.recorder import (
            get_instance,  # type: ignore[attr-defined,no-redef]
        )
    except ImportError:  # pragma: no cover
        try:
            from homeassistant.components.recorder.util import (  # type: ignore[no-redef]
                get_instance,
            )
        except ImportError:
            get_instance = None
    if get_instance is None:  # pragma: no cover - recorder always available
        raise RecorderUnavailableError()
    try:
        return get_instance(hass)
    except (KeyError, RuntimeError, AttributeError) as err:
        _LOGGER.debug("Recorder instance unavailable: %s", err)
        raise RecorderUnavailableError() from err


def recorder_is_available(hass: HomeAssistant) -> bool:
    """Return True when statistics can be read or written."""
    try:
        recorder_instance(hass)
    except RecorderUnavailableError:
        return False
    return True


async def async_statistic_metadata(
    hass: HomeAssistant, statistic_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Return metadata for the requested statistic ids."""
    from homeassistant.components.recorder.statistics import (
        async_list_statistic_ids,
    )

    if not statistic_ids:
        return {}
    metadata = await async_list_statistic_ids(hass, set(statistic_ids))
    return {item["statistic_id"]: item for item in metadata}


async def async_all_statistic_metadata(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return metadata for every cumulative (``has_sum``) statistic.

    Used by a scan with ``scope: energy`` or ``scope: all``.  Only cumulative
    statistics can be corrupted by a source that briefly reported ``0``, so
    mean-only statistics are never returned.
    """
    from homeassistant.components.recorder.statistics import async_list_statistic_ids

    if not recorder_is_available(hass):
        raise RecorderUnavailableError()
    try:
        return await async_list_statistic_ids(hass, statistic_type="sum")
    except TypeError:  # pragma: no cover - older Home Assistant signature
        metadata = await async_list_statistic_ids(hass)
        return [item for item in metadata if item.get("has_sum")]


async def async_statistics_rows(
    hass: HomeAssistant,
    statistic_ids: list[str],
    start: datetime,
    end: datetime | None,
    units: dict[str, str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Read hourly statistics rows (blocking call executed in the executor)."""
    from homeassistant.components.recorder.statistics import statistics_during_period

    if not statistic_ids:
        return {}
    return await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        end,
        set(statistic_ids),
        PERIOD_HOUR,
        units,
        set(STATISTIC_TYPES),
    )


def stored_unit(metadata: dict[str, Any]) -> str | None:
    """Return the unit a statistic is stored in."""
    unit = metadata.get("statistics_unit_of_measurement")
    if unit is None:
        unit = metadata.get("unit_of_measurement")
    return unit


def display_unit(metadata: dict[str, Any]) -> str | None:
    """Return the unit a statistic is displayed in."""
    return (
        metadata.get("display_unit_of_measurement")
        or metadata.get("statistics_unit_of_measurement")
        or metadata.get("unit_of_measurement")
    )


def as_utc(value: datetime | str | float | int | None) -> datetime | None:
    """Return an aware UTC datetime.

    Accepts the values the recorder returns (datetimes in older Home Assistant
    versions, POSIX timestamps in newer ones), ISO strings from service calls
    and naive datetimes (treated as local time).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return dt_util.utc_from_timestamp(float(value))
    if isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        if parsed is None:
            return None
        value = parsed
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return dt_util.as_utc(value)


def iso_timestamp(value: datetime | str | float | int | None) -> str | None:
    """Return an ISO timestamp for a recorder value."""
    moment = as_utc(value)
    return moment.isoformat() if moment else None


def sum_at(rows: list[dict[str, Any]], target: datetime) -> float | None:
    """Return the sum of the row at (or right after) ``target``."""
    for row in rows:
        start = as_utc(row["start"])
        if start is not None and start >= target and row.get("sum") is not None:
            return float(row["sum"])
    return None


def last_sum(rows: list[dict[str, Any]]) -> float | None:
    """Return the newest sum of a list of rows."""
    for row in reversed(rows):
        if row.get("sum") is not None:
            return float(row["sum"])
    return None


def anchor_sums(rows: list[dict[str, Any]], target: datetime) -> dict[str, float]:
    """Return ``{row start isoformat: sum}`` anchors used to verify a repair."""
    anchors: dict[str, float] = {}
    at_start = sum_at(rows, target)
    if at_start is not None:
        anchors["first_after_start"] = at_start
    for row in reversed(rows):
        start = as_utc(row["start"])
        if start is not None and row.get("sum") is not None:
            anchors[f"row:{start.isoformat()}"] = float(row["sum"])
            break
    return anchors


async def verify_adjustment_applied(
    hass: HomeAssistant,
    *,
    statistic_id: str,
    start_time: datetime,
    adjustment: float,
    anchors_before: dict[str, float],
) -> tuple[float | None, float | None, bool | None]:
    """Read the statistic back until the offset shows up (bounded retries).

    Returns ``(sum at start_time, newest sum, verified)``.  ``verified`` is
    ``None`` when the recorder could not be read well enough to compare.
    """
    tolerance = max(ALREADY_REPAIRED_TOLERANCE, abs(adjustment) * 1e-6)
    after_at_start: float | None = None
    after_last: float | None = None
    for attempt in range(VERIFY_ATTEMPTS):
        await async_wait_for_recorder(hass)
        rows = (
            await async_statistics_rows(
                hass, [statistic_id], start_time, dt_util.utcnow()
            )
        ).get(statistic_id, [])
        after_at_start = sum_at(rows, start_time)
        after_last = last_sum(rows)
        if after_at_start is not None:
            previous = anchors_before.get("first_after_start")
            if previous is not None and (
                abs((after_at_start - previous) - adjustment) <= tolerance
            ):
                return after_at_start, after_last, True
        anchors_after = anchor_sums(rows, start_time)
        for key, value in anchors_after.items():
            previous = anchors_before.get(key)
            if previous is not None and (
                abs((value - previous) - adjustment) <= tolerance
            ):
                return after_at_start, after_last, True
        if attempt + 1 < VERIFY_ATTEMPTS:
            await asyncio.sleep(VERIFY_DELAY)
    if after_at_start is None and after_last is None:
        return None, None, None
    if not anchors_before:
        return after_at_start, after_last, None
    return after_at_start, after_last, False


async def async_wait_for_recorder(
    hass: HomeAssistant, wait_seconds: float = 60.0
) -> None:
    """Wait (bounded) until the recorder queue has been processed."""
    recorder = recorder_instance(hass)
    if recorder is None:
        return
    try:
        async with asyncio.timeout(wait_seconds):
            await recorder.async_block_till_done()
    except TimeoutError:
        _LOGGER.warning(
            "Recorder did not finish writing within %.0f seconds; statistics may "
            "still be catching up. Re-check the statistic before repairing again.",
            wait_seconds,
        )
        return
    except Exception as err:  # pragma: no cover - defensive
        _LOGGER.debug("Recorder did not report completion: %s", err)
        return
    await hass.async_block_till_done()
