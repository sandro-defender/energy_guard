"""Config flow for Energy Guard.

Everything is configured through the UI: no YAML is required for normal use.

Flow
----
1. ``user``      - pick one or more cumulative energy sensors to protect.
2. ``naming``    - optional: adjust the names of the protected sensors.
3. ``detection`` - optional: tune the detection thresholds (defaults are fine).

The resulting config entry stores all definitions in ``entry.options`` so that
the options flow can edit them without touching the config entry data.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import TextSelector

from .const import (
    CONF_DERIVED,
    CONF_DETECTION,
    CONF_PROTECTED,
    CONF_SOURCE,
    DEFAULT_UNIT,
    DOMAIN,
    NAME,
)
from .migrations import CURRENT_VERSION
from .models import DetectionRules, SensorDefinition
from .selectors import (
    ENERGY_ENTITY_SELECTOR,
    detection_schema,
    entity_id_for,
    name_in_use,
)
from .units import is_energy_unit

_LOGGER = logging.getLogger(__name__)

CONF_NAMES = "sensor_names"
CONF_DETECTION_DEFAULTS = "detection_defaults"


class EnergyGuardConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Energy Guard config flow (single instance, no YAML)."""

    #: Bumped when stored options need a persisted rewrite; the matching handler
    #: is ``migrations.async_migrate_entry``.
    VERSION = CURRENT_VERSION

    def __init__(self) -> None:
        """Initialise the flow."""
        self._sources: list[str] = []
        self._names: dict[str, str] = {}
        self._detection: dict[str, Any] = {}

    # ------------------------------------------------------------------
    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        from .options_flow import EnergyGuardOptionsFlow

        return EnergyGuardOptionsFlow()

    # ------------------------------------------------------------------
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the energy sensors that should be protected."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}
        if user_input is not None:
            sources = list(dict.fromkeys(user_input[CONF_SOURCE]))
            problems: list[str] = []
            for source in sources:
                state = self.hass.states.get(source)
                if state is None:
                    problems.append(source)
                    continue
                unit = state.attributes.get("unit_of_measurement")
                if unit is not None and not is_energy_unit(unit):
                    problems.append(f"{source} ({unit})")
            if not sources:
                errors["base"] = "no_sources_selected"
            elif problems:
                _LOGGER.debug("Energy Guard ignored invalid sources: %s", problems)
                errors["base"] = "invalid_sources"
            else:
                self._sources = sources
                self._names = {
                    source: _default_name(self.hass, source) for source in sources
                }
                return await self.async_step_naming()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SOURCE): ENERGY_ENTITY_SELECTOR,
                }
            ),
            errors=errors,
            description_placeholders={
                "count": str(_count_energy_sensors(self.hass)),
                "integration": NAME,
            },
        )

    # ------------------------------------------------------------------
    async def async_step_naming(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user adjust the names of the created entities."""
        if user_input is not None:
            names = {
                source: user_input.get(f"name_{index}", "").strip() or default
                for index, (source, default) in enumerate(self._names.items())
            }
            # A name whose entity id is already taken (the source itself, or
            # another protected sensor) would be silently renamed with a "_2"
            # suffix by Home Assistant, so it is refused here.
            errors: dict[str, str] = {}
            for index, (source, name) in enumerate(names.items()):
                taken = [source, *(item for item in self._sources if item != source)]
                taken.extend(
                    entity_id_for(other)
                    for other_source, other in names.items()
                    if other_source != source
                )
                if name_in_use(name, taken):
                    errors[f"name_{index}"] = "name_in_use"
            if errors:
                return self.async_show_form(
                    step_id="naming",
                    data_schema=vol.Schema(
                        {
                            vol.Optional(f"name_{index}", default=name): TextSelector()
                            for index, name in enumerate(names.values())
                        }
                    ),
                    errors=errors,
                    description_placeholders={
                        "sources": ", ".join(str(source) for source in self._sources)
                    },
                )
            self._names = names
            return await self.async_step_detection()

        fields: dict[Any, Any] = {}
        for index, (_source, default) in enumerate(self._names.items()):
            fields[vol.Optional(f"name_{index}", default=default)] = TextSelector()
        return self.async_show_form(
            step_id="naming",
            data_schema=vol.Schema(fields),
            description_placeholders={
                "sources": ", ".join(str(source) for source in self._sources)
            },
        )

    # ------------------------------------------------------------------
    async def async_step_detection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the detection thresholds (defaults are recommended)."""
        if user_input is not None:
            self._detection = dict(user_input)
            return self._async_create_entry()

        return self.async_show_form(
            step_id="detection",
            data_schema=detection_schema(dict(DetectionRules().to_dict())),
            description_placeholders={
                "sensors": str(len(self._sources)),
            },
        )

    # ------------------------------------------------------------------
    @callback
    def _async_create_entry(self) -> ConfigFlowResult:
        """Create the config entry with all definitions."""
        protected: list[dict[str, Any]] = []
        for source in self._sources:
            definition = SensorDefinition(
                id=uuid.uuid4().hex,
                name=self._names.get(source) or _default_name(self.hass, source),
                source_entity_id=source,
                unit_of_measurement=_default_unit(self.hass, source),
                enabled=True,
            )
            protected.append(definition.to_dict())

        options = {
            CONF_PROTECTED: protected,
            CONF_DERIVED: [],
            CONF_DETECTION: self._detection,
        }
        return self.async_create_entry(
            title=f"{NAME} ({len(protected)} sensor(s))",
            data={},
            options=options,
        )


def _count_energy_sensors(hass: HomeAssistant) -> int:
    """Return the number of cumulative energy sensors in the state machine."""
    count = 0
    for state in hass.states.async_all("sensor"):
        if state.attributes.get("device_class") != "energy":
            continue
        if state.attributes.get("state_class") not in ("total_increasing", "total"):
            continue
        count += 1
    return count


def _default_name(hass: HomeAssistant, entity_id: str) -> str:
    """Return a friendly default name for a protected sensor."""
    state = hass.states.get(entity_id)
    friendly = None
    if state is not None:
        friendly = state.attributes.get("friendly_name")
    base = friendly or entity_id.split(".")[-1].replace("_", " ")
    base = str(base).replace("energy", "").strip() or str(base)
    return f"{base} protected".strip()


def _default_unit(hass: HomeAssistant, entity_id: str) -> str:
    """Return the unit a protected sensor should use."""
    state = hass.states.get(entity_id)
    if state is not None:
        unit = state.attributes.get("unit_of_measurement")
        if isinstance(unit, str) and is_energy_unit(unit):
            return unit
    return DEFAULT_UNIT
