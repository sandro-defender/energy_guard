"""Cost (monetary) repair helpers.

The Energy Dashboard stores cost as its *own* statistic (usually
``<energy statistic>_cost``) with its own ``sum``.  A false kWh offset therefore
also creates a false monetary offset, but the two are independent records:
repairing kWh does **not** repair the money.

Everything in this module is therefore only a *suggestion*:

* :func:`cost_offset` converts a confirmed kWh offset into the matching money
  offset using the configured tariff,
* :func:`effective_price` reads that tariff (fixed value or a price entity),
* :func:`cost_suggestion` builds the preview entry, which is always marked with
  ``separate_confirmation_required: True``.

The service layer only applies a monetary offset when the user passes
``confirm_cost: true`` on top of ``confirm: true``, and it writes its own
backup of the cost statistic first.

Worked example (the amount used throughout the documentation)::

    false energy : 38,243.46 kWh
    tariff       : 0.235 GEL/kWh
    correction   : 8,987.21 GEL
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .models import CostRepairConfig
from .protect import parse_float

#: Money is rounded to this many decimals when previewing offsets.
MONEY_PRECISION = 4


def cost_offset(energy_offset: float, price: float) -> float:
    """Return the money offset that mirrors an energy offset.

    A repair offset is *added* to the statistic, so a false positive energy
    offset (positive) becomes a negative money offset and vice versa.  The sign
    therefore always follows the energy offset, scaled by the tariff.
    """
    if price <= 0:
        return 0.0
    return round(float(energy_offset) * float(price), MONEY_PRECISION)


def money(value: float | None, currency: str | None = None) -> str:
    """Return a human readable money string."""
    if value is None:
        return "unknown"
    suffix = (currency or "").strip()
    text = f"{value:,.2f}"
    return f"{text} {suffix}".strip()


def tariff_line(
    false_energy: float | None, price: float, currency: str | None = None
) -> str:
    """Return the worked example used in previews and reports."""
    if false_energy is None:
        return "no false energy detected"
    unit = (currency or "").strip()
    per = f"{unit}/kWh" if unit else "per kWh"
    return (
        f"{abs(false_energy):,.2f} kWh x {price} {per} = "
        f"{money(abs(false_energy) * price, unit)}"
    )


def effective_price(hass: HomeAssistant, config: CostRepairConfig) -> float | None:
    """Return the tariff to use.

    ``price_entity_id`` wins when it exists and reports a usable number (a
    dynamic tariff), otherwise the fixed ``price`` is used.  Returns ``None``
    when neither is usable, so callers can skip cost handling instead of
    inventing a price.
    """
    if config.price_entity_id:
        state = hass.states.get(config.price_entity_id)
        if state is not None:
            value = parse_float(state.state)
            if value is not None and value > 0:
                return value
    if config.price and config.price > 0:
        return config.price
    return None


def cost_suggestion(
    *,
    cost_statistic_id: str,
    start_time: datetime,
    energy_offset: float,
    price: float,
    currency: str | None,
    fingerprint: str,
    energy_statistic_id: str | None = None,
) -> dict[str, Any]:
    """Build the cost preview entry for one energy offset."""
    unit = (currency or "").strip()
    offset = cost_offset(energy_offset, price)
    suggestion: dict[str, Any] = {
        "statistic_id": cost_statistic_id,
        "start_time": dt_util.as_utc(start_time).isoformat(),
        "offset": offset,
        "unit": unit,
        "reason": (
            f"Mirror of {abs(energy_offset):,.2f} kWh false energy at "
            f"{price} {unit}/kWh".replace(" /kWh", "/kWh")
        ),
        "false_energy_kwh": round(abs(float(energy_offset)), 6),
        "price": price,
        "worked_example": tariff_line(energy_offset, price, unit),
        "source_energy_offset": energy_offset,
        "fingerprint": fingerprint,
        "separate_confirmation_required": True,
        "create_backup": True,
        "note": (
            "Cost statistics are repaired separately: pass confirm_cost: true "
            "in addition to confirm: true to apply this offset."
        ),
    }
    if energy_statistic_id:
        suggestion["energy_statistic_id"] = energy_statistic_id
    return suggestion


def build_cost_suggestions(
    candidates: Iterable[Any],
    *,
    cost_statistic_id: str,
    price: float,
    currency: str | None,
    energy_statistic_id: str | None = None,
    fingerprint_fn: Any,
) -> list[dict[str, Any]]:
    """Return the cost previews for every candidate of the linked statistic."""
    suggestions: list[dict[str, Any]] = []
    for candidate in candidates:
        if energy_statistic_id and candidate.statistic_id != energy_statistic_id:
            continue
        suggestions.append(
            cost_suggestion(
                cost_statistic_id=cost_statistic_id,
                start_time=candidate.start_time,
                energy_offset=candidate.offset,
                price=price,
                currency=currency,
                fingerprint=fingerprint_fn(
                    cost_statistic_id,
                    candidate.start_time,
                    cost_offset(candidate.offset, price),
                    (currency or "").strip(),
                ),
                energy_statistic_id=candidate.statistic_id,
            )
        )
    return suggestions
