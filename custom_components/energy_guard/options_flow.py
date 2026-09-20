"""Options flow for Energy Guard.

Sections (menu entries):

* Protected Sensors
* Derived Sensors
* Utility Meters
* Detection Rules
* Statistics Repair
* Cost Repair
* Backups and Reports
* Review current issues
* Export YAML templates

Every section can add, edit, enable/disable and delete its own entries.  Only
objects that Energy Guard created are ever touched - user YAML/templates are
never read or written.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .config_api import clean_optional, taken_entity_ids
from .const import (
    CONF_BACKUPS,
    CONF_COST,
    CONF_DERIVED,
    CONF_DETECTION,
    CONF_ENABLED,
    CONF_ID,
    CONF_MODE,
    CONF_NAME,
    CONF_PARTS,
    CONF_PROTECTED,
    CONF_SCALE,
    CONF_SOURCE,
    CONF_SOURCES,
    CONF_TOTAL,
    CONF_UTILITY_METERS,
    MODE_PHASE_SPLIT,
    SECTION_BACKUPS,
    SECTION_COST,
    SECTION_DERIVED,
    SECTION_DETECTION,
    SECTION_EXPORT,
    SECTION_METERS,
    SECTION_PROTECTED,
    SECTION_REVIEW,
    SECTION_STATISTICS,
    TEMPLATES_FILE,
)
from .export import render_templates
from .hub import get_hub
from .selectors import (
    backups_schema,
    cost_schema,
    derived_schema,
    detection_schema,
    name_in_use,
    protected_schema,
    statistics_schema,
    utility_meter_schema,
)

_LOGGER = logging.getLogger(__name__)

MENU_OPTIONS = [
    SECTION_PROTECTED,
    SECTION_DERIVED,
    SECTION_METERS,
    SECTION_DETECTION,
    SECTION_STATISTICS,
    SECTION_COST,
    SECTION_BACKUPS,
    SECTION_REVIEW,
    SECTION_EXPORT,
]

ACTION_ADD = "add"
ACTION_EDIT = "edit"
ACTION_TOGGLE = "toggle"
ACTION_DELETE = "delete"
ACTION_BACK = "back_to_menu"

ITEM_SELECTOR = SelectSelector(
    SelectSelectorConfig(options=[], mode=SelectSelectorMode.DROPDOWN)
)


class EnergyGuardOptionsFlow(OptionsFlowWithReload):
    """Handle the Energy Guard options."""

    def __init__(self) -> None:
        """Initialise the options flow."""
        self._pending: dict[str, Any] = {}
        self._selected_id: str | None = None

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @property
    def _options(self) -> dict[str, Any]:
        """Return a mutable copy of the current options."""
        return dict(self.config_entry.options)

    def _list(self, key: str) -> list[dict[str, Any]]:
        """Return a list section of the options."""
        return list(self.config_entry.options.get(key, []))

    def _store(self, key: str, items: list[dict[str, Any]]) -> ConfigFlowResult:
        """Persist a list section and close the flow (the entry reloads)."""
        options = self._options
        options[key] = items
        return self.async_create_entry(title="", data=options)

    def _store_section(self, key: str, section: dict[str, Any]) -> ConfigFlowResult:
        """Persist a dict section (merged with the existing values)."""
        options = self._options
        current = dict(options.get(key) or {})
        current.update(section)
        options[key] = current
        return self.async_create_entry(title="", data=options)

    def _item_options(self, key: str) -> list[SelectOptionDict]:
        """Return select options for a list section."""
        options: list[SelectOptionDict] = []
        for item in self._list(key):
            name = item.get(CONF_NAME) or item.get(CONF_ID)
            details = item.get(CONF_SOURCE) or ", ".join(
                item.get(CONF_SOURCES) or item.get(CONF_PARTS) or []
            )
            state = "" if item.get(CONF_ENABLED, True) else " (disabled)"
            options.append(
                SelectOptionDict(
                    value=item[CONF_ID],
                    label=f"{name}{state} - {details}" if details else f"{name}{state}",
                )
            )
        return options

    def _find(self, key: str, item_id: str) -> dict[str, Any] | None:
        """Return one item of a list section."""
        for item in self._list(key):
            if item.get(CONF_ID) == item_id:
                return dict(item)
        return None

    def _taken_entity_ids(
        self, payload: dict[str, Any], *, ignore_id: str | None = None
    ) -> list[str]:
        """Return entity ids a new/edited definition must not collide with.

        The rule itself lives in :mod:`config_api` so that the options flow and
        the configuration panel cannot drift apart.
        """
        return taken_entity_ids(
            dict(self.config_entry.options), payload, ignore_id=ignore_id
        )

    def _label(self, item: dict[str, Any]) -> str:
        """Return a display label for an item."""
        return str(item.get(CONF_NAME) or item.get(CONF_ID) or "unnamed")

    @staticmethod
    def _clean_optional(payload: dict[str, Any]) -> dict[str, Any]:
        """Turn empty strings into None and parse the numeric optionals."""
        return clean_optional(payload)

    # ------------------------------------------------------------------
    # menu
    # ------------------------------------------------------------------
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the main menu."""
        hub = get_hub(self.hass, self.config_entry.entry_id)
        protected = len(self._list(CONF_PROTECTED))
        derived = len(self._list(CONF_DERIVED))
        meters = len(self._list(CONF_UTILITY_METERS))
        issues = 0
        if hub is not None:
            issues = len(hub.scan_candidates) + hub.anomaly_count
        return self.async_show_menu(
            step_id="init",
            menu_options=MENU_OPTIONS,
            description_placeholders={
                "protected": str(protected),
                "derived": str(derived),
                "meters": str(meters),
                "issues": str(issues),
            },
        )

    # ------------------------------------------------------------------
    # protected sensors
    # ------------------------------------------------------------------
    async def async_step_protected_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Protected sensors submenu."""
        return self._async_show_section_menu(SECTION_PROTECTED)

    @callback
    def _async_show_section_menu(
        self, step_id: str, *, message: str | None = None
    ) -> ConfigFlowResult:
        """Show the add/edit/toggle/delete menu of a section."""
        key = self._section_key(step_id)
        items = self._list(key)
        menu: list[str] = [f"{step_id}_add"]
        if items:
            menu.extend(
                [
                    f"{step_id}_edit",
                    f"{step_id}_toggle",
                    f"{step_id}_delete",
                ]
            )
        return self.async_show_menu(
            step_id=step_id,
            menu_options=menu,
            description_placeholders={
                "count": str(len(items)),
                "items": ", ".join(self._label(item) for item in items) or "none",
            },
        )

    def _section_key(self, step_id: str) -> str:
        """Map a step id to an options key."""
        return {
            SECTION_PROTECTED: CONF_PROTECTED,
            SECTION_DERIVED: CONF_DERIVED,
            SECTION_METERS: CONF_UTILITY_METERS,
        }.get(step_id, step_id)

    # -- add -----------------------------------------------------------
    async def async_step_protected_sensors_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a protected sensor."""
        if user_input is not None:
            name = (user_input.get(CONF_NAME) or "").strip()
            errors: dict[str, str] = {}
            if not name:
                errors["base"] = "name_required"
            elif name_in_use(name, self._taken_entity_ids(user_input)):
                errors["base"] = "name_in_use"
            if errors:
                return self.async_show_form(
                    step_id="protected_sensors_add",
                    data_schema=protected_schema(dict(user_input)),
                    errors=errors,
                )
            item = self._clean_optional(dict(user_input))
            item[CONF_ID] = uuid.uuid4().hex
            item[CONF_NAME] = name
            item.setdefault(CONF_SCALE, 1.0)
            items = self._list(CONF_PROTECTED)
            items.append(item)
            return self._store(CONF_PROTECTED, items)

        return self.async_show_form(
            step_id="protected_sensors_add",
            data_schema=protected_schema(),
        )

    # -- edit ----------------------------------------------------------
    async def async_step_protected_sensors_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select and edit a protected sensor."""
        return await self._async_select(
            user_input,
            step_id="protected_sensors_edit",
            section=SECTION_PROTECTED,
            key=CONF_PROTECTED,
            next_step="protected_sensors_edit_form",
        )

    async def async_step_protected_sensors_edit_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the selected protected sensor."""
        item = self._find(CONF_PROTECTED, self._selected_id or "")
        if item is None:
            return self._async_show_section_menu(SECTION_PROTECTED)
        if user_input is not None:
            updated = self._clean_optional(dict(user_input))
            updated[CONF_ID] = item[CONF_ID]
            updated[CONF_NAME] = (updated.get(CONF_NAME) or self._label(item)).strip()
            if name_in_use(
                updated[CONF_NAME],
                self._taken_entity_ids(updated, ignore_id=item[CONF_ID]),
            ):
                return self.async_show_form(
                    step_id="protected_sensors_edit_form",
                    data_schema=protected_schema(updated),
                    errors={"base": "name_in_use"},
                    description_placeholders={
                        "name": self._label(item),
                        "id": item[CONF_ID],
                    },
                )
            updated.setdefault(CONF_SCALE, 1.0)
            items = [
                updated if existing.get(CONF_ID) == item[CONF_ID] else existing
                for existing in self._list(CONF_PROTECTED)
            ]
            return self._store(CONF_PROTECTED, items)
        return self.async_show_form(
            step_id="protected_sensors_edit_form",
            data_schema=protected_schema(item),
            description_placeholders={
                "name": self._label(item),
                "id": item[CONF_ID],
            },
        )

    # -- toggle --------------------------------------------------------
    async def async_step_protected_sensors_toggle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select and enable/disable a protected sensor."""
        return await self._async_select(
            user_input,
            step_id="protected_sensors_toggle",
            section=SECTION_PROTECTED,
            key=CONF_PROTECTED,
            next_step="protected_sensors_toggle_confirm",
        )

    async def async_step_protected_sensors_toggle_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Toggle the enabled flag."""
        item = self._find(CONF_PROTECTED, self._selected_id or "")
        if item is None:
            return self._async_show_section_menu(SECTION_PROTECTED)
        if user_input is not None:
            if not user_input.get("confirm"):
                return self.async_show_form(
                    step_id="protected_sensors_toggle_confirm",
                    data_schema=vol.Schema(
                        {vol.Required("confirm"): BooleanSelector()}
                    ),
                    errors={"confirm": "confirmation_required"},
                )
            item[CONF_ENABLED] = not item.get(CONF_ENABLED, True)
            items = [
                item if existing.get(CONF_ID) == item[CONF_ID] else existing
                for existing in self._list(CONF_PROTECTED)
            ]
            return self._store(CONF_PROTECTED, items)
        state = "enabled" if item.get(CONF_ENABLED, True) else "disabled"
        return self.async_show_form(
            step_id="protected_sensors_toggle_confirm",
            data_schema=vol.Schema({vol.Required("confirm"): BooleanSelector()}),
            description_placeholders={"name": self._label(item), "state": state},
        )

    # -- delete --------------------------------------------------------
    async def async_step_protected_sensors_delete(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select and delete a protected sensor."""
        return await self._async_select(
            user_input,
            step_id="protected_sensors_delete",
            section=SECTION_PROTECTED,
            key=CONF_PROTECTED,
            next_step="protected_sensors_delete_confirm",
        )

    async def async_step_protected_sensors_delete_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm deleting a protected sensor."""
        item = self._find(CONF_PROTECTED, self._selected_id or "")
        if item is None:
            return self._async_show_section_menu(SECTION_PROTECTED)
        if user_input is not None:
            if not user_input.get("confirm"):
                return self.async_show_form(
                    step_id="protected_sensors_delete_confirm",
                    data_schema=vol.Schema(
                        {vol.Required("confirm"): BooleanSelector()}
                    ),
                    errors={"confirm": "confirmation_required"},
                )
            items = [
                existing
                for existing in self._list(CONF_PROTECTED)
                if existing.get(CONF_ID) != item[CONF_ID]
            ]
            return self._store(CONF_PROTECTED, items)
        return self.async_show_form(
            step_id="protected_sensors_delete_confirm",
            data_schema=vol.Schema({vol.Required("confirm"): BooleanSelector()}),
            description_placeholders={"name": self._label(item)},
        )

    # ------------------------------------------------------------------
    # derived sensors
    # ------------------------------------------------------------------
    async def async_step_derived_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Derived sensors submenu."""
        return self._async_show_section_menu(SECTION_DERIVED)

    async def async_step_derived_sensors_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a derived sensor."""
        if user_input is not None:
            name = (user_input.get(CONF_NAME) or "").strip()
            mode = user_input.get(CONF_MODE)
            sources = list(user_input.get(CONF_SOURCES) or [])
            parts = list(user_input.get(CONF_PARTS) or [])
            total = user_input.get(CONF_TOTAL)
            errors: dict[str, str] = {}
            if not name:
                errors["base"] = "name_required"
            elif name_in_use(name, self._taken_entity_ids(dict(user_input))):
                errors["base"] = "name_in_use"
            elif mode == MODE_PHASE_SPLIT and not (total and parts):
                errors["base"] = "phase_split_needs_total_and_parts"
            elif mode != MODE_PHASE_SPLIT and len(sources) < 2:
                errors["base"] = "needs_two_sources"
            if errors:
                return self.async_show_form(
                    step_id="derived_sensors_add",
                    data_schema=derived_schema({**dict(user_input)}),
                    errors=errors,
                )
            item = self._clean_optional(dict(user_input))
            item[CONF_ID] = uuid.uuid4().hex
            item[CONF_NAME] = name
            items = self._list(CONF_DERIVED)
            items.append(item)
            return self._store(CONF_DERIVED, items)
        return self.async_show_form(
            step_id="derived_sensors_add", data_schema=derived_schema()
        )

    async def async_step_derived_sensors_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a derived sensor to edit."""
        return await self._async_select(
            user_input,
            step_id="derived_sensors_edit",
            section=SECTION_DERIVED,
            key=CONF_DERIVED,
            next_step="derived_sensors_edit_form",
        )

    async def async_step_derived_sensors_edit_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit a derived sensor."""
        item = self._find(CONF_DERIVED, self._selected_id or "")
        if item is None:
            return self._async_show_section_menu(SECTION_DERIVED)
        if user_input is not None:
            updated = self._clean_optional(dict(user_input))
            updated[CONF_ID] = item[CONF_ID]
            updated[CONF_NAME] = (updated.get(CONF_NAME) or self._label(item)).strip()
            if name_in_use(
                updated[CONF_NAME],
                self._taken_entity_ids(updated, ignore_id=item[CONF_ID]),
            ):
                return self.async_show_form(
                    step_id="derived_sensors_edit_form",
                    data_schema=derived_schema(updated),
                    errors={"base": "name_in_use"},
                    description_placeholders={
                        "name": self._label(item),
                        "id": item[CONF_ID],
                    },
                )
            items = [
                updated if existing.get(CONF_ID) == item[CONF_ID] else existing
                for existing in self._list(CONF_DERIVED)
            ]
            return self._store(CONF_DERIVED, items)
        return self.async_show_form(
            step_id="derived_sensors_edit_form",
            data_schema=derived_schema(item),
            description_placeholders={"name": self._label(item)},
        )

    async def async_step_derived_sensors_toggle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a derived sensor to enable/disable."""
        return await self._async_select(
            user_input,
            step_id="derived_sensors_toggle",
            section=SECTION_DERIVED,
            key=CONF_DERIVED,
            next_step="derived_sensors_toggle_confirm",
        )

    async def async_step_derived_sensors_toggle_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Toggle a derived sensor."""
        item = self._find(CONF_DERIVED, self._selected_id or "")
        if item is None:
            return self._async_show_section_menu(SECTION_DERIVED)
        if user_input is not None and user_input.get("confirm"):
            item[CONF_ENABLED] = not item.get(CONF_ENABLED, True)
            items = [
                item if existing.get(CONF_ID) == item[CONF_ID] else existing
                for existing in self._list(CONF_DERIVED)
            ]
            return self._store(CONF_DERIVED, items)
        return self.async_show_form(
            step_id="derived_sensors_toggle_confirm",
            data_schema=vol.Schema({vol.Required("confirm"): BooleanSelector()}),
            description_placeholders={
                "name": self._label(item),
                "state": "enabled" if item.get(CONF_ENABLED, True) else "disabled",
            },
        )

    async def async_step_derived_sensors_delete(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a derived sensor to delete."""
        return await self._async_select(
            user_input,
            step_id="derived_sensors_delete",
            section=SECTION_DERIVED,
            key=CONF_DERIVED,
            next_step="derived_sensors_delete_confirm",
        )

    async def async_step_derived_sensors_delete_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm deleting a derived sensor."""
        item = self._find(CONF_DERIVED, self._selected_id or "")
        if item is None:
            return self._async_show_section_menu(SECTION_DERIVED)
        if user_input is not None and user_input.get("confirm"):
            items = [
                existing
                for existing in self._list(CONF_DERIVED)
                if existing.get(CONF_ID) != item[CONF_ID]
            ]
            return self._store(CONF_DERIVED, items)
        return self.async_show_form(
            step_id="derived_sensors_delete_confirm",
            data_schema=vol.Schema({vol.Required("confirm"): BooleanSelector()}),
            description_placeholders={"name": self._label(item)},
        )

    # ------------------------------------------------------------------
    # utility meters
    # ------------------------------------------------------------------
    async def async_step_utility_meters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Utility meter submenu."""
        return self._async_show_section_menu(SECTION_METERS)

    async def async_step_utility_meters_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a utility meter entry used for calibration."""
        if user_input is not None:
            name = (user_input.get(CONF_NAME) or "").strip()
            if not name:
                return self.async_show_form(
                    step_id="utility_meters_add",
                    data_schema=utility_meter_schema(dict(user_input)),
                    errors={"base": "name_required"},
                )
            item = dict(user_input)
            item[CONF_ID] = uuid.uuid4().hex
            item[CONF_NAME] = name
            items = self._list(CONF_UTILITY_METERS)
            items.append(item)
            return self._store(CONF_UTILITY_METERS, items)
        return self.async_show_form(
            step_id="utility_meters_add", data_schema=utility_meter_schema()
        )

    async def async_step_utility_meters_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a utility meter to edit."""
        return await self._async_select(
            user_input,
            step_id="utility_meters_edit",
            section=SECTION_METERS,
            key=CONF_UTILITY_METERS,
            next_step="utility_meters_edit_form",
        )

    async def async_step_utility_meters_edit_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit a utility meter entry."""
        item = self._find(CONF_UTILITY_METERS, self._selected_id or "")
        if item is None:
            return self._async_show_section_menu(SECTION_METERS)
        if user_input is not None:
            updated = dict(user_input)
            updated[CONF_ID] = item[CONF_ID]
            updated[CONF_NAME] = (updated.get(CONF_NAME) or self._label(item)).strip()
            items = [
                updated if existing.get(CONF_ID) == item[CONF_ID] else existing
                for existing in self._list(CONF_UTILITY_METERS)
            ]
            return self._store(CONF_UTILITY_METERS, items)
        return self.async_show_form(
            step_id="utility_meters_edit_form",
            data_schema=utility_meter_schema(item),
            description_placeholders={"name": self._label(item)},
        )

    async def async_step_utility_meters_toggle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a utility meter to enable/disable."""
        return await self._async_select(
            user_input,
            step_id="utility_meters_toggle",
            section=SECTION_METERS,
            key=CONF_UTILITY_METERS,
            next_step="utility_meters_toggle_confirm",
        )

    async def async_step_utility_meters_toggle_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Toggle a utility meter entry."""
        item = self._find(CONF_UTILITY_METERS, self._selected_id or "")
        if item is None:
            return self._async_show_section_menu(SECTION_METERS)
        if user_input is not None and user_input.get("confirm"):
            item[CONF_ENABLED] = not item.get(CONF_ENABLED, True)
            items = [
                item if existing.get(CONF_ID) == item[CONF_ID] else existing
                for existing in self._list(CONF_UTILITY_METERS)
            ]
            return self._store(CONF_UTILITY_METERS, items)
        return self.async_show_form(
            step_id="utility_meters_toggle_confirm",
            data_schema=vol.Schema({vol.Required("confirm"): BooleanSelector()}),
            description_placeholders={
                "name": self._label(item),
                "state": "enabled" if item.get(CONF_ENABLED, True) else "disabled",
            },
        )

    async def async_step_utility_meters_delete(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a utility meter to delete."""
        return await self._async_select(
            user_input,
            step_id="utility_meters_delete",
            section=SECTION_METERS,
            key=CONF_UTILITY_METERS,
            next_step="utility_meters_delete_confirm",
        )

    async def async_step_utility_meters_delete_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm deleting a utility meter entry."""
        item = self._find(CONF_UTILITY_METERS, self._selected_id or "")
        if item is None:
            return self._async_show_section_menu(SECTION_METERS)
        if user_input is not None and user_input.get("confirm"):
            items = [
                existing
                for existing in self._list(CONF_UTILITY_METERS)
                if existing.get(CONF_ID) != item[CONF_ID]
            ]
            return self._store(CONF_UTILITY_METERS, items)
        return self.async_show_form(
            step_id="utility_meters_delete_confirm",
            data_schema=vol.Schema({vol.Required("confirm"): BooleanSelector()}),
            description_placeholders={"name": self._label(item)},
        )

    # ------------------------------------------------------------------
    # sections with a single form
    # ------------------------------------------------------------------
    async def async_step_detection_rules(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the live detection rules."""
        current = dict(self.config_entry.options.get(CONF_DETECTION) or {})
        if user_input is not None:
            return self._store_section(CONF_DETECTION, dict(user_input))
        return self.async_show_form(
            step_id=SECTION_DETECTION,
            data_schema=detection_schema(current),
        )

    async def async_step_statistics_repair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the statistics scanner thresholds."""
        hub = get_hub(self.hass, self.config_entry.entry_id)
        current = dict(self.config_entry.options.get(CONF_DETECTION) or {})
        if user_input is not None:
            return self._store_section(CONF_DETECTION, dict(user_input))
        candidates = len(hub.scan_candidates) if hub is not None else 0
        last_scan = (
            hub.last_scan.isoformat() if hub is not None and hub.last_scan else "never"
        )
        return self.async_show_form(
            step_id=SECTION_STATISTICS,
            data_schema=statistics_schema(current),
            description_placeholders={
                "candidates": str(candidates),
                "last_scan": last_scan,
            },
        )

    async def async_step_cost_repair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the fixed tariff used to mirror kWh repairs into money."""
        current = dict(self.config_entry.options.get(CONF_COST) or {})
        if user_input is not None:
            return self._store_section(CONF_COST, dict(user_input))
        return self.async_show_form(
            step_id=SECTION_COST,
            data_schema=cost_schema(current),
        )

    async def async_step_backups_reports(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure backups and reports."""
        current = dict(self.config_entry.options.get(CONF_BACKUPS) or {})
        if user_input is not None:
            return self._store_section(CONF_BACKUPS, dict(user_input))
        hub = get_hub(self.hass, self.config_entry.entry_id)
        return self.async_show_form(
            step_id=SECTION_BACKUPS,
            data_schema=backups_schema(current),
            description_placeholders={
                "backup_dir": str(hub.backup_dir) if hub else "n/a",
                "report_dir": str(hub.report_dir) if hub else "n/a",
            },
        )

    async def async_step_review(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the current issues and the exact service calls to run."""
        hub = get_hub(self.hass, self.config_entry.entry_id)
        if hub is None:
            return self.async_abort(reason="not_loaded")
        candidates = hub.scan_candidates
        events = hub.events_since(hub.issue_window_start)
        lines = [
            f"- {item.get('statistic_id')} @ {item.get('start_time')}: offset "
            f"{item.get('offset')} {item.get('unit')} "
            f"({', '.join(item.get('evidence', []))})"
            for item in candidates[:10]
        ]
        preview = (
            "\n".join(lines)
            if lines
            else "No suspicious statistics offsets were found."
        )
        return self.async_show_form(
            step_id=SECTION_REVIEW,
            data_schema=vol.Schema({}),
            description_placeholders={
                "issues": str(len(candidates)),
                "anomalies": str(len(events)),
                "estimated_false_energy": str(hub.estimated_false_energy),
                "candidate_list": preview,
                "repair_hint": (
                    "Nothing is repaired automatically. Review the candidates above, "
                    "then call the service energy_guard.repair_statistics with "
                    "confirm: true, or export a report with "
                    "energy_guard.export_repair_report."
                ),
            },
            last_step=True,
        )

    async def async_step_export_yaml(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Write the template YAML export (read-only for user files)."""
        hub = get_hub(self.hass, self.config_entry.entry_id)
        if hub is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            if user_input.get("write"):
                content = render_templates(
                    hub.config.protected, hub.config.derived, hub.config.utility_meters
                )
                path = hub.base_dir / TEMPLATES_FILE
                await self.hass.async_add_executor_job(_write, path, content)
                return self.async_abort(
                    reason="yaml_written",
                    description_placeholders={"path": str(path)},
                )
            return self.async_abort(reason="yaml_not_written")
        return self.async_show_form(
            step_id=SECTION_EXPORT,
            data_schema=vol.Schema(
                {vol.Optional("write", default=False): BooleanSelector()}
            ),
            description_placeholders={
                "path": str(hub.base_dir / TEMPLATES_FILE),
                "count": str(len(hub.config.protected) + len(hub.config.derived)),
            },
        )

    # ------------------------------------------------------------------
    # shared select step
    # ------------------------------------------------------------------
    async def _async_select(
        self,
        user_input: dict[str, Any] | None,
        *,
        step_id: str,
        section: str,
        key: str,
        next_step: str,
    ) -> ConfigFlowResult:
        """Show a dropdown with the items of a section, then continue."""
        items = self._item_options(key)
        if not items:
            return self._async_show_section_menu(section)
        if user_input is not None:
            self._selected_id = str(user_input[CONF_ID])
            return await getattr(self, f"async_step_{next_step}")(None)
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=items,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            description_placeholders={
                "action": step_id.rsplit("_", 1)[-1],
                "count": str(len(items)),
            },
        )


def _write(path: Any, content: str) -> None:
    """Write the export file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
