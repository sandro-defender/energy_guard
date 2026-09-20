"""Cost (monetary) repair tests: kWh -> GEL conversion and the separate gate."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from homeassistant.core import HomeAssistant

from custom_components.energy_guard.costs import (
    build_cost_suggestions,
    cost_offset,
    effective_price,
    money,
    tariff_line,
)
from custom_components.energy_guard.detection import fingerprint
from custom_components.energy_guard.models import (
    CostRepairConfig,
    ScanCandidate,
)

FALSE_ENERGY = 38243.46
PRICE = 0.235
EXPECTED_GEL = 8987.2131


def test_cost_offset_matches_the_documented_example() -> None:
    """38,243.46 kWh at 0.235 GEL/kWh is 8,987.21 GEL."""
    assert cost_offset(-FALSE_ENERGY, PRICE) == pytest.approx(-EXPECTED_GEL)
    assert round(abs(cost_offset(-FALSE_ENERGY, PRICE)), 2) == 8987.21
    # The sign always follows the energy offset.
    assert cost_offset(FALSE_ENERGY, PRICE) == pytest.approx(EXPECTED_GEL)
    assert cost_offset(-FALSE_ENERGY, 0) == 0.0


def test_money_and_tariff_line_formatting() -> None:
    """Previews use thousands separators and the configured currency."""
    assert money(-EXPECTED_GEL, "GEL") == "-8,987.21 GEL"
    assert money(None, "GEL") == "unknown"
    assert (
        tariff_line(-FALSE_ENERGY, PRICE, "GEL")
        == "38,243.46 kWh x 0.235 GEL/kWh = 8,987.21 GEL"
    )


def test_cost_suggestion_always_requires_its_own_confirmation() -> None:
    """A cost suggestion is a preview: it never claims to be applied."""
    start = datetime(2026, 9, 18, 4, 0, tzinfo=UTC)
    suggestion = build_cost_suggestions(
        [
            ScanCandidate(
                statistic_id="sensor.grid_import",
                start_time=start,
                detected_at=start,
                unit="kWh",
                offset=-FALSE_ENERGY,
                severity="error",
            )
        ],
        cost_statistic_id="sensor.grid_import_cost",
        price=PRICE,
        currency="GEL",
        energy_statistic_id="sensor.grid_import",
        fingerprint_fn=fingerprint,
    )[0]

    assert suggestion["separate_confirmation_required"] is True
    assert suggestion["create_backup"] is True
    assert suggestion["offset"] == pytest.approx(-EXPECTED_GEL)
    assert suggestion["unit"] == "GEL"
    assert suggestion["false_energy_kwh"] == pytest.approx(FALSE_ENERGY)
    assert suggestion["worked_example"] == (
        "38,243.46 kWh x 0.235 GEL/kWh = 8,987.21 GEL"
    )
    assert "confirm_cost" in suggestion["note"]
    assert len(suggestion["fingerprint"]) == 16


def test_cost_suggestions_ignore_other_statistics() -> None:
    """Only candidates of the linked energy statistic produce money offsets."""
    start = datetime(2026, 9, 18, 4, 0, tzinfo=UTC)
    candidates = [
        ScanCandidate(
            statistic_id="sensor.grid_export",
            start_time=start,
            detected_at=start,
            unit="kWh",
            offset=-100.0,
        )
    ]
    assert (
        build_cost_suggestions(
            candidates,
            cost_statistic_id="sensor.grid_import_cost",
            price=PRICE,
            currency="GEL",
            energy_statistic_id="sensor.grid_import",
            fingerprint_fn=fingerprint,
        )
        == []
    )


def test_effective_price_prefers_a_live_price_entity(hass: HomeAssistant) -> None:
    """A dynamic tariff entity wins over the fixed price, with a safe fallback."""
    config = CostRepairConfig(
        enabled=True,
        price=PRICE,
        currency="GEL",
        price_entity_id="sensor.tariff",
    )
    # No state yet -> fixed price.
    assert effective_price(hass, config) == pytest.approx(PRICE)

    hass.states.async_set("sensor.tariff", "0.31", {"unit_of_measurement": "GEL/kWh"})
    assert effective_price(hass, config) == pytest.approx(0.31)

    # A broken price entity falls back to the fixed price instead of guessing.
    hass.states.async_set("sensor.tariff", "unknown")
    assert effective_price(hass, config) == pytest.approx(PRICE)

    hass.states.async_set("sensor.tariff", "0")
    assert effective_price(hass, config) == pytest.approx(PRICE)


def test_effective_price_is_none_without_any_tariff(hass: HomeAssistant) -> None:
    """No tariff at all means "skip cost handling", never a made up price."""
    assert effective_price(hass, CostRepairConfig(enabled=True)) is None
