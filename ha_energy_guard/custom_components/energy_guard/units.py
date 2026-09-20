"""Unit helpers for Energy Guard.

Energy Guard only ever *reads* energy statistics.  Keeping the conversion logic
in one small module makes sure the same factor is used by the protection engine,
the statistics scanner and the repair engine.
"""

from __future__ import annotations

import logging
from typing import Final

from .const import ENERGY_UNIT_TO_KWH

_LOGGER = logging.getLogger(__name__)

_HA_CONVERTER: object | None = None
_HA_CONVERTER_CHECKED: bool = False


def _ha_energy_converter() -> object | None:
    """Return Home Assistant's energy converter when available."""
    global _HA_CONVERTER, _HA_CONVERTER_CHECKED
    if _HA_CONVERTER_CHECKED:
        return _HA_CONVERTER
    _HA_CONVERTER_CHECKED = True
    try:  # pragma: no cover - depends on the installed Home Assistant version
        from homeassistant.util.unit_conversion import EnergyConverter

        _HA_CONVERTER = EnergyConverter
    except ImportError:  # pragma: no cover
        _HA_CONVERTER = None
    return _HA_CONVERTER


TRUE_UNITS: Final = ("true", "false", "on", "off")


def normalize_unit(unit: str | None) -> str:
    """Return a lower-case unit string for lookups."""
    return (unit or "").strip().lower()


def is_energy_unit(unit: str | None) -> bool:
    """Return True when ``unit`` is a known energy unit."""
    if unit is None:
        return False
    key = normalize_unit(unit)
    if key in ENERGY_UNIT_TO_KWH:
        return True
    converter = _ha_energy_converter()
    valid_units = getattr(converter, "VALID_UNITS", None)
    return bool(valid_units and unit in valid_units)


def unit_factor(from_unit: str | None, to_unit: str | None) -> float:
    """Return the factor that converts ``from_unit`` into ``to_unit``.

    Unknown or non-convertible units return ``1.0`` so that a mis-configured
    unit never silently scales a user's energy data.
    """
    if from_unit is None or to_unit is None:
        return 1.0
    if from_unit == to_unit:
        return 1.0

    converter = _ha_energy_converter()
    if converter is not None:
        try:
            return float(
                converter.convert(  # type: ignore[attr-defined]
                    1.0, from_unit, to_unit
                )
            )
        except Exception:
            pass

    source = ENERGY_UNIT_TO_KWH.get(normalize_unit(from_unit))
    target = ENERGY_UNIT_TO_KWH.get(normalize_unit(to_unit))
    if source is None or target is None:
        _LOGGER.debug(
            "Cannot convert %s to %s, assuming a factor of 1.0", from_unit, to_unit
        )
        return 1.0
    return source / target


def convert(value: float, from_unit: str | None, to_unit: str | None) -> float:
    """Convert ``value`` between two units."""
    return value * unit_factor(from_unit, to_unit)


def units_are_compatible(first: str | None, second: str | None) -> bool:
    """Return True when both units are of the same (energy) family or identical."""
    if first == second:
        return True
    return is_energy_unit(first) and is_energy_unit(second)
