"""JSON backups of recorder statistics.

Every statistic that Energy Guard is about to change is written to a JSON file
first.  Backups are plain files in ``<config>/energy_guard/backups/`` so a user
can inspect, copy or restore them without Energy Guard being involved, and the
newest :data:`~custom_components.energy_guard.models.BackupConfig.keep_backups`
files are kept.

A backup is *never* applied automatically.  Rolling back means calling
``energy_guard.repair_statistics`` with the inverse offset (returned in the
``rollback`` section of the repair response) or restoring the rows manually.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import VERSION
from .recorder_io import as_utc, async_statistics_rows

_LOGGER = logging.getLogger(__name__)

#: Rows written per backup file (protects against huge databases).
MAX_BACKUP_ROWS = 100_000


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------
def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON to disk (executor)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")


def write_unique_json(directory: Path, filename: str, payload: dict[str, Any]) -> str:
    """Write a JSON file without ever overwriting an existing backup.

    Two repairs of the same statistic inside the same second would otherwise
    produce the same file name and destroy the older (and possibly more
    valuable) backup.  A numeric suffix is added until the name is free.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    counter = 1
    while path.exists():
        path = directory / f"{Path(filename).stem}_{counter}.json"
        counter += 1
    path.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
    return str(path)


def write_text(path: Path, text: str) -> None:
    """Write text to disk (executor)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def prune_backups(directory: Path, keep: int) -> None:
    """Keep only the newest ``keep`` backup files."""
    if keep <= 0:
        return
    try:
        files = sorted(
            directory.glob("statistics_backup_*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError:  # pragma: no cover - filesystem race
        return
    for stale in files[keep:]:
        try:
            stale.unlink()
        except OSError:  # pragma: no cover
            _LOGGER.debug("Could not remove old backup %s", stale)


async def async_backup_statistics(
    hass: HomeAssistant,
    *,
    directory: Path,
    statistic_id: str,
    unit: str,
    start: datetime,
    end: datetime,
    keep: int = 25,
    reason: str = "statistics repair",
) -> dict[str, Any]:
    """Write a JSON backup of all rows that a repair would touch."""
    rows = (await async_statistics_rows(hass, [statistic_id], start, end)).get(
        statistic_id, []
    )
    payload_rows = [
        {
            "start": as_utc(row["start"]).isoformat(),
            "sum": row.get("sum"),
            "state": row.get("state"),
            "last_reset": (
                as_utc(row["last_reset"]).isoformat()
                if row.get("last_reset") is not None
                else None
            ),
        }
        for row in rows[:MAX_BACKUP_ROWS]
    ]
    checksum = hashlib.sha256(
        json.dumps(payload_rows, sort_keys=True).encode()
    ).hexdigest()
    created = dt_util.utcnow()
    filename = (
        f"statistics_backup_{created.strftime('%Y%m%dT%H%M%SZ')}_"
        f"{statistic_id.replace('.', '_').replace(':', '_')}.json"
    )
    payload = {
        "energy_guard": {
            "version": VERSION,
            "created_at": created.isoformat(),
            "reason": reason,
            "statistic_id": statistic_id,
            "unit": unit,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "rows": len(payload_rows),
            "rows_truncated": len(rows) > MAX_BACKUP_ROWS,
            "checksum": f"sha256:{checksum}",
            "restore_hint": (
                "Apply the inverse adjustment with energy_guard.repair_statistics to "
                "roll this statistic back."
            ),
        },
        "rows": payload_rows,
    }
    written = await hass.async_add_executor_job(
        write_unique_json, directory, filename, payload
    )
    await hass.async_add_executor_job(prune_backups, directory, keep)
    _LOGGER.info("Energy Guard wrote a statistics backup to %s", written)
    return {
        "file": written,
        "name": Path(written).name,
        "rows": len(payload_rows),
        "checksum": f"sha256:{checksum}",
        "created_at": created.isoformat(),
        "unit": unit,
        "statistic_id": statistic_id,
    }
