"""Energy Guard - protect Home Assistant Energy Dashboard data.

Energy Guard creates protected copies of cumulative energy sensors and never
lets a reconnect artefact (``unavailable`` -> ``0`` -> previous lifetime value)
reach the recorder, utility meters or the Energy Dashboard.  It also detects and
- only when explicitly confirmed - repairs statistics that were already
corrupted before Energy Guard was installed.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DATA_HUBS, DOMAIN, NAME, PLATFORMS, VERSION
from .coordinator import EnergyGuardCoordinator
from .hub import EnergyGuardHub, get_hubs
from .migrations import async_migrate_entry
from .panel import async_register_panel, async_unregister_panel
from .repairs import async_delete_repair_issues
from .services import async_register_services, async_unregister_services
from .websocket import (
    async_register_websocket_api,
    async_unregister_websocket_api,
)

_LOGGER = logging.getLogger(__name__)

PLATFORM_LIST = [Platform.SENSOR, Platform.BINARY_SENSOR]

# Energy Guard is set up exclusively from a config entry (no YAML options);
# this schema rejects YAML configuration under the `energy_guard:` key.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

__all__ = [
    "DOMAIN",
    "PLATFORMS",
    "async_migrate_entry",
    "async_setup",
    "async_setup_entry",
    "async_unload_entry",
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Energy Guard integration (no YAML configuration)."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(DATA_HUBS, {})
    _LOGGER.debug("%s %s loaded (config flow only)", NAME, VERSION)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Energy Guard from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hubs: dict[str, EnergyGuardHub] = hass.data[DOMAIN].setdefault(DATA_HUBS, {})

    hub = EnergyGuardHub(hass, entry)
    hub.coordinator = EnergyGuardCoordinator(hass, hub)
    await hub.async_load()
    hubs[entry.entry_id] = hub
    entry.runtime_data = hub

    async_register_services(hass)
    # The sidebar configuration panel is optional: it talks to this API, which
    # uses exactly the same validation and storage as the options flow.
    async_register_websocket_api(hass)
    await async_register_panel(hass)

    # The first refresh is read-only and never raises: a missing recorder just
    # results in an empty scan result so that setup can continue.
    await hub.coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORM_LIST)

    async def _async_handle_stop(_event: Event) -> None:
        """Persist the diagnostic log on shutdown."""
        await hub.async_save()
        await hub.coordinator.async_shutdown()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_handle_stop)
    )

    _LOGGER.info(
        "%s %s started with %d protected and %d derived sensor(s)",
        NAME,
        VERSION,
        len(hub.config.protected),
        len(hub.config.derived),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORM_LIST)
    if not unload_ok:
        return False

    await async_delete_repair_issues(hass, entry.entry_id)

    hubs = get_hubs(hass)
    hub = hubs.pop(entry.entry_id, None)
    if hub is not None:
        await hub.async_save()
        if hub.coordinator is not None:
            await hub.coordinator.async_shutdown()
    if not hubs:
        async_unregister_services(hass)
        async_unregister_websocket_api(hass)
        await async_unregister_panel(hass)
    return True
