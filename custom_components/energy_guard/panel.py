"""Sidebar configuration panel for Energy Guard.

The integration registers one optional sidebar entry ("Energy Guard") whose web
component is served from ``frontend/energy_guard-panel.js``.  The panel is a
thin client of the WebSocket API in :mod:`websocket` - all validation and
storage happens in Python, so the panel cannot store anything the options flow
would refuse.

The panel is *optional* and *best effort*:

* it is only registered when Home Assistant has a frontend and the
  ``panel_custom`` integration, so a headless setup keeps working;
* a failure to register is logged and ignored - protection, scanning and
  repairing never depend on the panel;
* it is registered with ``require_admin=True`` and removed on unload.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, NAME, VERSION

_LOGGER = logging.getLogger(__name__)

#: URL the panel is reachable at: ``/energy-guard-config``.
PANEL_URL_PATH: Final = "energy-guard-config"
PANEL_TITLE: Final = NAME
PANEL_ICON: Final = "mdi:shield-cog-outline"
PANEL_ELEMENT: Final = "energy-guard-config-panel"
PANEL_JS_URL: Final = "/energy_guard/energy_guard-panel.js"

_FRONTEND_DIR: Final = Path(__file__).parent / "frontend"
_REGISTERED = f"{DOMAIN}_panel_registered"


def _frontend_ready(hass: HomeAssistant) -> bool:
    """Return True when a sidebar panel can be registered at all."""
    components = hass.config.components
    return "frontend" in components and "panel_custom" in components


async def async_register_panel(hass: HomeAssistant) -> None:
    """Serve the panel asset and register the sidebar entry (best effort).

    Never raises: a Home Assistant without a frontend (or an unexpected API
    change) must not stop the integration from protecting data.
    """
    if hass.data.setdefault(DOMAIN, {}).get(_REGISTERED):
        return
    if not _frontend_ready(hass):
        _LOGGER.debug(
            "%s configuration panel skipped: no frontend/panel_custom available", NAME
        )
        return
    try:
        from homeassistant.components.panel_custom import async_register_panel

        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    PANEL_JS_URL, str(_FRONTEND_DIR / "energy_guard-panel.js"), False
                )
            ]
        )
        await async_register_panel(
            hass,
            frontend_url_path=PANEL_URL_PATH,
            webcomponent_name=PANEL_ELEMENT,
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            module_url=PANEL_JS_URL,
            config={"version": VERSION},
            require_admin=True,
        )
    except Exception as err:
        _LOGGER.warning(
            "%s could not register its configuration panel (%s). Everything else "
            "keeps working; the same settings are available in the options flow.",
            NAME,
            err,
        )
        return
    hass.data[DOMAIN][_REGISTERED] = True
    _LOGGER.debug("%s configuration panel available at /%s", NAME, PANEL_URL_PATH)


async def async_unregister_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar entry (called when the last entry unloads)."""
    if not hass.data.setdefault(DOMAIN, {}).pop(_REGISTERED, None):
        return
    try:
        from homeassistant.components.frontend import async_remove_panel

        async_remove_panel(hass, PANEL_URL_PATH)
    except Exception as err:
        _LOGGER.debug("%s could not remove its configuration panel: %s", NAME, err)


@callback
def panel_is_registered(hass: HomeAssistant) -> bool:
    """Return True when the sidebar panel is registered."""
    return bool(hass.data.setdefault(DOMAIN, {}).get(_REGISTERED))


__all__ = [
    "PANEL_ELEMENT",
    "PANEL_JS_URL",
    "PANEL_TITLE",
    "PANEL_URL_PATH",
    "async_register_panel",
    "async_unregister_panel",
    "panel_is_registered",
]
