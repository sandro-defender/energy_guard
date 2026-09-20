"""Selector based schemas shared by the config and options flows.

Keeping every form definition in one module means the config flow, the options
flow and the translations cannot drift apart: each schema is built from named
helpers (``optional_number``, ``_unit_selector``, ...) and every key it emits has
a matching entry in ``strings.json`` (checked by ``tests/test_contracts.py``).

Numbers are always optional text fields: a Home Assistant ``NumberSelector``
rejects an empty string, and "leave this at the default" must stay possible.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import voluptuous as vol
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .const import (
    CONF_ACCEPT_RESET,
    CONF_BASELINE,
    CONF_BASELINE_AT,
    CONF_CONFIRM_SCANS,
    CONF_COST_STATISTIC_ID,
    CONF_CURRENCY,
    CONF_CYCLE,
    CONF_DO_NOT_DECREASE,
    CONF_ENABLED,
    CONF_ENERGY_STATISTIC_ID,
    CONF_EVENT_RETENTION_DAYS,
    CONF_GRACE_PERIOD,
    CONF_ISSUE_WINDOW_HOURS,
    CONF_LARGE_JUMP,
    CONF_LARGE_JUMP_RATIO,
    CONF_LOOKBACK_HOURS,
    CONF_MAX_VALUE,
    CONF_MODE,
    CONF_NAME,
    CONF_OFFSET,
    CONF_PARTS,
    CONF_PRECISION,
    CONF_PRICE,
    CONF_PRICE_ENTITY,
    CONF_RECOVERY_HOLD_SCANS,
    CONF_REJECT_LARGE_JUMPS,
    CONF_REQUIRE_ALL_SOURCES,
    CONF_SCALE,
    CONF_SCAN_INTERVAL,
    CONF_SCAN_SCOPE,
    CONF_SIMULTANEOUS_THRESHOLD,
    CONF_SIMULTANEOUS_WINDOW_MINUTES,
    CONF_SOURCE,
    CONF_SOURCES,
    CONF_STAT_JUMP_KWH,
    CONF_STAT_JUMP_RATIO,
    CONF_STAT_SUM_STATE_RATIO,
    CONF_TARGET_UNIT,
    CONF_TOTAL,
    CONF_UTILITY_METER,
    CONF_ZERO_MIN_PREVIOUS,
    DEFAULT_ACCEPT_RESET,
    DEFAULT_CONFIRM_SCANS,
    DEFAULT_CURRENCY,
    DEFAULT_CURRENCY_SYMBOL,
    DEFAULT_DO_NOT_DECREASE,
    DEFAULT_EVENT_RETENTION_DAYS,
    DEFAULT_GRACE_PERIOD,
    DEFAULT_ISSUE_WINDOW_HOURS,
    DEFAULT_LARGE_JUMP,
    DEFAULT_LARGE_JUMP_RATIO,
    DEFAULT_LOOKBACK_HOURS,
    DEFAULT_PRECISION,
    DEFAULT_RECOVERY_HOLD_SCANS,
    DEFAULT_REJECT_LARGE_JUMPS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SCAN_SCOPE,
    DEFAULT_SIMULTANEOUS_THRESHOLD,
    DEFAULT_SIMULTANEOUS_WINDOW_MINUTES,
    DEFAULT_STAT_JUMP_KWH,
    DEFAULT_STAT_JUMP_RATIO,
    DEFAULT_STAT_SUM_STATE_RATIO,
    DEFAULT_UNIT,
    DEFAULT_ZERO_MIN_PREVIOUS,
    DERIVED_MODES,
    ENERGY_UNITS,
    MODE_DIFFERENCE,
    MODE_PHASE_SPLIT,
    MODE_SUM,
    SCAN_SCOPES,
)


class OptionalEntitySelector(EntitySelector):
    """An entity selector that also accepts "nothing selected".

    Home Assistant's ``EntitySelector`` rejects ``None`` ("Entity None is
    neither a valid entity ID nor a valid UUID").  An *optional* field of a
    flow is validated against its default, and for an optional entity field
    that default is ``None`` - so a form could not be submitted at all unless
    every optional entity field was filled in.  This subclass maps an empty
    selection (``None``, ``""`` or ``[]``) to ``None`` and delegates everything
    else to the strict selector, so an invalid entity id is still rejected.
    """

    def __call__(self, data: Any) -> Any:
        """Return ``None`` for an empty selection, otherwise validate it."""
        if data is None or data == "" or data == []:
            return None
        return super().__call__(data)


def required_field(key: str, value: Any, selector: Any) -> tuple:
    """Return a required field; a default is only passed when one exists.

    ``vol.Required(key, default=None)`` sends ``None`` through the validator,
    which makes an unfilled form fail with the selector's message instead of
    "required key not provided".
    """
    if value in (None, "", []):
        return vol.Required(key), selector
    return vol.Required(key, default=value), selector


ENERGY_ENTITY_SELECTOR = EntitySelector(
    EntitySelectorConfig(
        domain="sensor",
        device_class=["energy"],
        multiple=True,
    )
)

SINGLE_ENERGY_ENTITY_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="sensor", device_class=["energy"])
)

ANY_SENSOR_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="sensor", multiple=True)
)

SINGLE_SENSOR_SELECTOR = EntitySelector(EntitySelectorConfig(domain="sensor"))

# Used for *optional* entity fields (a field that may be left empty).
OPTIONAL_SENSOR_SELECTOR = OptionalEntitySelector(EntitySelectorConfig(domain="sensor"))
OPTIONAL_ENERGY_ENTITY_SELECTOR = OptionalEntitySelector(
    EntitySelectorConfig(domain="sensor", device_class=["energy"])
)

UTILITY_METER_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="sensor", integration="utility_meter")
)

UNIT_OPTIONS = [SelectOptionDict(value=unit, label=unit) for unit in ENERGY_UNITS]

MODE_OPTIONS = [
    SelectOptionDict(value=MODE_SUM, label="Sum of the selected sensors"),
    SelectOptionDict(value=MODE_DIFFERENCE, label="Difference (first minus the rest)"),
    SelectOptionDict(value=MODE_PHASE_SPLIT, label="Phase split (total minus phases)"),
]


def _number(
    minimum: float = 0.0,
    maximum: float | None = None,
    *,
    step: float | str = "any",
    unit: str | None = None,
    mode: NumberSelectorMode = NumberSelectorMode.BOX,
) -> NumberSelector:
    """Return a number selector.

    Unset optional values (``maximum``/``unit``) are *omitted*: Home Assistant
    validates the selector config, and an explicit ``max: None`` makes the
    whole form fail to render (``expected float at 'max'``).  Omitting the key
    keeps the field optional for the user.
    """
    config: dict[str, Any] = {"min": minimum, "step": step, "mode": mode}
    if maximum is not None:
        config["max"] = maximum
    if unit is not None:
        config["unit_of_measurement"] = unit
    return NumberSelector(NumberSelectorConfig(**config))


def _int(minimum: int = 0, maximum: int | None = None) -> NumberSelector:
    """Return an integer number selector (optionally bounded)."""
    config: dict[str, Any] = {"min": minimum, "step": 1, "mode": NumberSelectorMode.BOX}
    if maximum is not None:
        config["max"] = maximum
    return NumberSelector(NumberSelectorConfig(**config))


def _text(multiline: bool = False) -> TextSelector:
    """Return a text selector."""
    return TextSelector(TextSelectorConfig(multiline=multiline))


def _unit_selector() -> SelectSelector:
    """Return the unit selector."""
    return SelectSelector(
        SelectSelectorConfig(
            options=UNIT_OPTIONS,
            mode=SelectSelectorMode.DROPDOWN,
            custom_value=False,
        )
    )


def _bool() -> BooleanSelector:
    """Return a boolean selector."""
    return BooleanSelector()


def optional_number(key: str, value: Any) -> tuple:
    """Return an optional numeric field (text based so it can be left empty)."""
    return vol.Optional(key, default="" if value is None else str(value)), _text()


def optional_datetime(key: str, value: Any) -> tuple:
    """Return an optional datetime field (text based)."""
    return vol.Optional(key, default=value or ""), _text()


# ---------------------------------------------------------------------------
# Protected sensors
# ---------------------------------------------------------------------------
def protected_schema(data: dict[str, Any] | None = None) -> vol.Schema:
    """Return the schema used to add/edit a protected sensor."""
    data = data or {}
    fields: dict[Any, Any] = {
        vol.Required(CONF_NAME, default=data.get(CONF_NAME, "")): _text(),
        required_field(
            CONF_SOURCE, data.get(CONF_SOURCE), SINGLE_ENERGY_ENTITY_SELECTOR
        )[0]: SINGLE_ENERGY_ENTITY_SELECTOR,
        vol.Optional(
            CONF_TARGET_UNIT, default=data.get(CONF_TARGET_UNIT, DEFAULT_UNIT)
        ): _unit_selector(),
        vol.Optional(CONF_OFFSET, default=data.get(CONF_OFFSET, 0.0)): _number(
            step="any"
        ),
        vol.Optional(CONF_ENABLED, default=data.get(CONF_ENABLED, True)): _bool(),
        vol.Optional(
            CONF_DO_NOT_DECREASE,
            default=data.get(CONF_DO_NOT_DECREASE, DEFAULT_DO_NOT_DECREASE),
        ): _bool(),
        vol.Optional(
            CONF_ACCEPT_RESET,
            default=data.get(CONF_ACCEPT_RESET, DEFAULT_ACCEPT_RESET),
        ): _bool(),
        vol.Optional(
            CONF_ZERO_MIN_PREVIOUS,
            default=data.get(CONF_ZERO_MIN_PREVIOUS, DEFAULT_ZERO_MIN_PREVIOUS),
        ): _number(step="any"),
        vol.Optional(
            CONF_CONFIRM_SCANS,
            default=data.get(CONF_CONFIRM_SCANS, DEFAULT_CONFIRM_SCANS),
        ): _int(1, 60),
        vol.Optional(
            CONF_RECOVERY_HOLD_SCANS,
            default=data.get(CONF_RECOVERY_HOLD_SCANS, DEFAULT_RECOVERY_HOLD_SCANS),
        ): _int(1, 60),
        vol.Optional(
            CONF_GRACE_PERIOD,
            default=data.get(CONF_GRACE_PERIOD, DEFAULT_GRACE_PERIOD),
        ): _int(
            0,
            3600,
        ),
        vol.Optional(
            CONF_LARGE_JUMP, default=data.get(CONF_LARGE_JUMP, DEFAULT_LARGE_JUMP)
        ): _number(step="any"),
        vol.Optional(
            CONF_LARGE_JUMP_RATIO,
            default=data.get(CONF_LARGE_JUMP_RATIO, DEFAULT_LARGE_JUMP_RATIO),
        ): _number(step="any"),
        vol.Optional(
            CONF_REJECT_LARGE_JUMPS,
            default=data.get(CONF_REJECT_LARGE_JUMPS, DEFAULT_REJECT_LARGE_JUMPS),
        ): _bool(),
        vol.Optional(
            CONF_PRECISION, default=data.get(CONF_PRECISION, DEFAULT_PRECISION)
        ): _int(0, 6),
    }
    fields.update([optional_number(CONF_MAX_VALUE, data.get(CONF_MAX_VALUE))])
    return vol.Schema(fields)


# ---------------------------------------------------------------------------
# Derived sensors
# ---------------------------------------------------------------------------
def derived_schema(data: dict[str, Any] | None = None) -> vol.Schema:
    """Return the schema used to add/edit a derived sensor."""
    data = data or {}
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=data.get(CONF_NAME, "")): _text(),
            vol.Optional(CONF_MODE, default=data.get(CONF_MODE, MODE_SUM)): (
                SelectSelector(
                    SelectSelectorConfig(
                        options=MODE_OPTIONS,
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="mode",
                    )
                )
            ),
            vol.Optional(
                CONF_SOURCES, default=data.get(CONF_SOURCES) or []
            ): ENERGY_ENTITY_SELECTOR,
            vol.Optional(
                CONF_TOTAL, default=data.get(CONF_TOTAL)
            ): OPTIONAL_ENERGY_ENTITY_SELECTOR,
            vol.Optional(
                CONF_PARTS, default=data.get(CONF_PARTS) or []
            ): ENERGY_ENTITY_SELECTOR,
            vol.Optional(CONF_SCALE, default=data.get(CONF_SCALE, 1.0)): _number(
                step="any"
            ),
            vol.Optional(CONF_OFFSET, default=data.get(CONF_OFFSET, 0.0)): _number(
                step="any"
            ),
            vol.Optional(
                CONF_TARGET_UNIT, default=data.get(CONF_TARGET_UNIT, DEFAULT_UNIT)
            ): _unit_selector(),
            vol.Optional(
                CONF_DO_NOT_DECREASE,
                default=data.get(CONF_DO_NOT_DECREASE, DEFAULT_DO_NOT_DECREASE),
            ): _bool(),
            vol.Optional(
                CONF_REQUIRE_ALL_SOURCES,
                default=data.get(CONF_REQUIRE_ALL_SOURCES, True),
            ): _bool(),
            vol.Optional(CONF_ENABLED, default=data.get(CONF_ENABLED, True)): _bool(),
        }
    )


# ---------------------------------------------------------------------------
# Utility meters
# ---------------------------------------------------------------------------
def utility_meter_schema(data: dict[str, Any] | None = None) -> vol.Schema:
    """Return the schema used to add/edit a utility meter entry."""
    data = data or {}
    fields: dict[Any, Any] = {
        vol.Required(CONF_NAME, default=data.get(CONF_NAME, "")): _text(),
        required_field(
            CONF_UTILITY_METER, data.get(CONF_UTILITY_METER), UTILITY_METER_SELECTOR
        )[0]: UTILITY_METER_SELECTOR,
        vol.Optional(
            CONF_SOURCE, default=data.get(CONF_SOURCE)
        ): OPTIONAL_SENSOR_SELECTOR,
        vol.Optional(CONF_BASELINE, default=data.get(CONF_BASELINE, 0.0)): _number(
            step="any"
        ),
        vol.Optional(CONF_CYCLE, default=data.get(CONF_CYCLE, "") or ""): _text(),
        vol.Optional(CONF_ENABLED, default=data.get(CONF_ENABLED, True)): _bool(),
    }
    fields[optional_datetime(CONF_BASELINE_AT, data.get(CONF_BASELINE_AT))[0]] = _text()
    return vol.Schema(fields)


# ---------------------------------------------------------------------------
# Detection rules / statistics repair / cost repair / backups
# ---------------------------------------------------------------------------
def detection_schema(data: dict[str, Any] | None = None) -> vol.Schema:
    """Return the schema for the live detection rules."""
    data = data or {}
    return vol.Schema(
        {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): _int(60, 86400),
            vol.Optional(
                CONF_LOOKBACK_HOURS,
                default=data.get(CONF_LOOKBACK_HOURS, DEFAULT_LOOKBACK_HOURS),
            ): _int(1, 8760),
            vol.Optional(
                CONF_ISSUE_WINDOW_HOURS,
                default=data.get(CONF_ISSUE_WINDOW_HOURS, DEFAULT_ISSUE_WINDOW_HOURS),
            ): _int(1, 8760),
            vol.Optional(
                CONF_SIMULTANEOUS_THRESHOLD,
                default=data.get(
                    CONF_SIMULTANEOUS_THRESHOLD, DEFAULT_SIMULTANEOUS_THRESHOLD
                ),
            ): _int(2, 50),
            vol.Optional(
                CONF_SIMULTANEOUS_WINDOW_MINUTES,
                default=data.get(
                    CONF_SIMULTANEOUS_WINDOW_MINUTES,
                    DEFAULT_SIMULTANEOUS_WINDOW_MINUTES,
                ),
            ): _int(1, 1440),
            vol.Optional(
                CONF_EVENT_RETENTION_DAYS,
                default=data.get(
                    CONF_EVENT_RETENTION_DAYS, DEFAULT_EVENT_RETENTION_DAYS
                ),
            ): _int(1, 365),
        }
    )


def _scan_scope_selector() -> SelectSelector:
    """Return the selector for the statistics scan scope."""
    return SelectSelector(
        SelectSelectorConfig(
            options=list(SCAN_SCOPES),
            mode=SelectSelectorMode.DROPDOWN,
            translation_key="scan_scope",
        )
    )


def statistics_schema(data: dict[str, Any] | None = None) -> vol.Schema:
    """Return the schema for the statistics scanner thresholds."""
    data = data or {}
    return vol.Schema(
        {
            vol.Optional(
                CONF_SCAN_SCOPE,
                default=data.get(CONF_SCAN_SCOPE, DEFAULT_SCAN_SCOPE),
            ): _scan_scope_selector(),
            vol.Optional(
                CONF_STAT_JUMP_KWH,
                default=data.get(CONF_STAT_JUMP_KWH, DEFAULT_STAT_JUMP_KWH),
            ): _number(step="any", unit="kWh"),
            vol.Optional(
                CONF_STAT_JUMP_RATIO,
                default=data.get(CONF_STAT_JUMP_RATIO, DEFAULT_STAT_JUMP_RATIO),
            ): _number(step="any"),
            vol.Optional(
                CONF_STAT_SUM_STATE_RATIO,
                default=data.get(
                    CONF_STAT_SUM_STATE_RATIO, DEFAULT_STAT_SUM_STATE_RATIO
                ),
            ): _number(step="any"),
        }
    )


def cost_schema(data: dict[str, Any] | None = None) -> vol.Schema:
    """Return the schema for the fixed tariff cost repair settings."""
    data = data or {}
    return vol.Schema(
        {
            vol.Optional(CONF_ENABLED, default=data.get(CONF_ENABLED, False)): _bool(),
            vol.Optional(CONF_PRICE, default=data.get(CONF_PRICE, 0.0)): _number(
                step="any"
            ),
            vol.Optional(
                CONF_CURRENCY, default=data.get(CONF_CURRENCY, DEFAULT_CURRENCY)
            ): _text(),
            vol.Optional(
                CONF_ENERGY_STATISTIC_ID, default=data.get(CONF_ENERGY_STATISTIC_ID, "")
            ): _text(),
            vol.Optional(
                CONF_COST_STATISTIC_ID, default=data.get(CONF_COST_STATISTIC_ID, "")
            ): _text(),
            vol.Optional(
                CONF_PRICE_ENTITY, default=data.get(CONF_PRICE_ENTITY, "")
            ): _text(),
        }
    )


def backups_schema(data: dict[str, Any] | None = None) -> vol.Schema:
    """Return the schema for backup and report settings."""
    data = data or {}
    return vol.Schema(
        {
            vol.Optional(
                "create_backup", default=data.get("create_backup", True)
            ): _bool(),
            vol.Optional("keep_backups", default=data.get("keep_backups", 25)): _int(
                0, 1000
            ),
        }
    )


def currency_symbol() -> str:
    """Return the default currency symbol."""
    return DEFAULT_CURRENCY_SYMBOL


def entity_id_for(name: str, domain: str = "sensor") -> str:
    """Return the entity id a definition with ``name`` will get."""
    return f"{domain}.{slugify_name(name)}"


def name_in_use(name: str, taken: Iterable[str]) -> bool:
    """Return True when ``name`` would produce an entity id that is taken.

    A protected sensor whose name matches an existing entity id gets a
    ``_2`` suffix from Home Assistant - confusing in the Energy Dashboard and
    easy to get wrong, so the flows refuse it instead.
    """
    entity_id = entity_id_for(name)
    return entity_id in {str(item) for item in taken}


def slugify_name(name: str) -> str:
    """Return a slug for a sensor name."""
    from homeassistant.util import slugify

    return slugify(name or "energy")


__all__ = [
    "ANY_SENSOR_SELECTOR",
    "DERIVED_MODES",
    "ENERGY_ENTITY_SELECTOR",
    "MODE_OPTIONS",
    "OPTIONAL_ENERGY_ENTITY_SELECTOR",
    "OPTIONAL_SENSOR_SELECTOR",
    "SINGLE_ENERGY_ENTITY_SELECTOR",
    "backups_schema",
    "cost_schema",
    "currency_symbol",
    "derived_schema",
    "detection_schema",
    "entity_id_for",
    "name_in_use",
    "optional_datetime",
    "optional_number",
    "protected_schema",
    "slugify_name",
    "statistics_schema",
    "utility_meter_schema",
]
