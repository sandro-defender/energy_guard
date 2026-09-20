"""Shared test fixtures for Energy Guard."""

from __future__ import annotations

import pathlib
import shutil
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_guard.migrations import CURRENT_VERSION

REPO_ROOT = pathlib.Path(__file__).parents[1]
INTEGRATION_DIR = REPO_ROOT / "custom_components" / "energy_guard"

pytest_plugins = "pytest_homeassistant_custom_component"

SOURCE = "sensor.grid_import"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make sure custom integrations can be loaded in every test."""


@pytest.fixture
def recorder_db_url() -> str:
    """Use the in-memory SQLite recorder database.

    pytest-homeassistant-custom-component asserts that the recorder database is
    requested before Home Assistant exists.  Energy Guard tests enable custom
    integrations with an autouse fixture (which creates Home Assistant first),
    so the assertion is bypassed here; the in-memory URL itself is unchanged.
    """
    return "sqlite://"


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir: str) -> str:
    """Return a temporary config dir that contains this integration.

    ``hass.config.path("energy_guard")`` (used for backups and reports) points
    into the temporary directory, so tests never write into the repository.
    """
    target = pathlib.Path(hass_tmp_config_dir) / "custom_components" / "energy_guard"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        INTEGRATION_DIR, target, ignore=shutil.ignore_patterns("__pycache__")
    )
    return hass_tmp_config_dir


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def protected_definition(
    source: str = SOURCE, name: str = "Grid import protected", **overrides: Any
) -> dict[str, Any]:
    """Return a protected sensor definition as stored in the options."""
    definition: dict[str, Any] = {
        "id": f"test_{source.split('.')[-1]}",
        "name": name,
        "enabled": True,
        "source_entity_id": source,
        "unit_of_measurement": "kWh",
        "offset": 0.0,
        "do_not_decrease": True,
        "accept_real_reset": False,
        "zero_min_previous": 1.0,
        "confirm_scans": 2,
        "recovery_hold_scans": 1,
        "grace_period": 0,
        "large_jump": 100000.0,
        "large_jump_ratio": 1000.0,
        "reject_large_jumps": False,
        "precision": 3,
    }
    definition.update(overrides)
    return definition


def make_entry(*, version: int = CURRENT_VERSION, **options: Any) -> MockConfigEntry:
    """Return a config entry with the given options.

    ``version`` maps to the config entry version (what Home Assistant compares
    with the flow version before calling ``async_migrate_entry``); every other
    keyword becomes an option, exactly like the options flow would store it.
    """
    return MockConfigEntry(
        domain="energy_guard",
        title="Energy Guard",
        data={},
        options=options,
        version=version,
        entry_id="test_entry_id",
    )


async def setup_guard(
    hass: HomeAssistant,
    *,
    protected: list[dict[str, Any]] | None = None,
    derived: list[dict[str, Any]] | None = None,
    utility_meters: list[dict[str, Any]] | None = None,
    detection: dict[str, Any] | None = None,
    cost: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Set up an Energy Guard config entry."""
    entry = make_entry(
        protected_sensors=protected if protected is not None else [],
        derived_sensors=derived or [],
        utility_meters=utility_meters or [],
        detection_rules=detection or {},
        cost_repair=cost or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def set_source(
    hass: HomeAssistant,
    entity_id: str,
    value: str | float,
    *,
    unit: str = "kWh",
    state_class: str = "total_increasing",
    device_class: str = "energy",
) -> None:
    """Write a state that looks like a cumulative energy sensor."""
    hass.states.async_set(
        entity_id,
        value,
        {
            "unit_of_measurement": unit,
            "state_class": state_class,
            "device_class": device_class,
            "friendly_name": entity_id.split(".")[-1].replace("_", " ").title(),
        },
    )


class StateRecorder(list):
    """Record every state an entity publishes (used to prove no false 0 is sent)."""

    def __init__(self, hass: HomeAssistant, entity_id: str) -> None:
        """Start recording."""
        super().__init__()
        self._remove = async_track_state_change_event(
            hass, [entity_id], self._handle_event
        )

    @callback
    def _handle_event(self, event) -> None:
        """Store the new state."""
        new_state = event.data.get("new_state")
        if new_state is not None:
            self.append(new_state.state)

    def stop(self) -> None:
        """Stop recording."""
        self._remove()

    @property
    def numeric(self) -> list[float]:
        """Return only the numeric values that were published."""
        values: list[float] = []
        for state in self:
            try:
                values.append(float(state))
            except (TypeError, ValueError):
                continue
        return values


def capture_states(hass: HomeAssistant, entity_id: str) -> StateRecorder:
    """Return a recorder for all future states of an entity."""
    return StateRecorder(hass, entity_id)
