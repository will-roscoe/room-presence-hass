"""Config flow (the room's sensors and phone), options flow (extra sensors and every timing) and
the ``place`` subentry flow (places outside the room)."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
)
from homeassistant.util import slugify
import voluptuous as vol

from .const import (
    CONF_BATHROOM,
    CONF_BATHROOM_WINDOW,
    CONF_BED_LEAVE_GRACE,
    CONF_CROSSING_WINDOW,
    CONF_DOOR_WITNESSES,
    CONF_ENTRY_NEEDS_CROSSING,
    CONF_ENTRY_WINDOW,
    CONF_EXIT_DOOR_ON_TIME,
    CONF_EXIT_PRE_WINDOW,
    CONF_EXIT_QUIET,
    CONF_FALLBACK_MOTION,
    CONF_FP2_BED,
    CONF_FP2_DESK,
    CONF_FP2_ROOM,
    CONF_MOVEMENT,
    CONF_NAME,
    CONF_PHONE_SSID,
    CONF_PHONE_TRACKER,
    CONF_REBUILD_LOOKBACK,
    CONF_RECENTLY_LEFT_DURATION,
    CONF_SLEEP_ENTITIES,
    CONF_SLEEP_QUIET,
    CONF_WAKE_ON_TIME,
    DEFAULTS,
    DOMAIN,
)
from .coordinator import SUBENTRY_PLACE
from .engine.machine import LOCATIONS_FIXED


def _number(maximum: float, unit: str, step: float = 1) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=0, max=maximum, step=step, unit_of_measurement=unit, mode=NumberSelectorMode.BOX
        )
    )


def _entity(domain: str | None = None, multiple: bool = False) -> EntitySelector:
    config = EntitySelectorConfig(multiple=multiple)
    if domain:
        config["domain"] = domain
    return EntitySelector(config)


#: Options-flow timing fields (seconds; ``rebuild_lookback`` in hours) and the entry flag.
OPTION_FIELDS: dict[str, Any] = {
    CONF_CROSSING_WINDOW: _number(600, "s"),
    CONF_BATHROOM_WINDOW: _number(600, "s"),
    CONF_RECENTLY_LEFT_DURATION: _number(3600, "s"),
    CONF_SLEEP_QUIET: _number(14400, "s"),
    CONF_WAKE_ON_TIME: _number(120, "s"),
    CONF_BED_LEAVE_GRACE: _number(600, "s"),
    CONF_EXIT_PRE_WINDOW: _number(1800, "s"),
    CONF_EXIT_QUIET: _number(1800, "s"),
    CONF_EXIT_DOOR_ON_TIME: _number(120, "s"),
    CONF_ENTRY_WINDOW: _number(600, "s"),
    CONF_REBUILD_LOOKBACK: _number(240, "h"),
    CONF_ENTRY_NEEDS_CROSSING: BooleanSelector(),
}

#: Optional options-flow sensors; no default, may be left empty.
SENSOR_FIELDS: dict[str, Any] = {
    CONF_BATHROOM: _entity("binary_sensor"),
    CONF_MOVEMENT: _entity("binary_sensor"),
    CONF_FALLBACK_MOTION: _entity("binary_sensor", multiple=True),
    CONF_SLEEP_ENTITIES: _entity(multiple=True),
}


def _drop_empty(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v not in (None, "", [])}


class RoomPresenceConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a tracked room: its FP2 zones, door witnesses and the phone."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_DOOR_WITNESSES):
                errors[CONF_DOOR_WITNESSES] = "door_witness_required"
            else:
                await self.async_set_unique_id(user_input[CONF_FP2_ROOM])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=_drop_empty(user_input)
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Room"): TextSelector(),
                vol.Required(CONF_FP2_ROOM): _entity("binary_sensor"),
                vol.Optional(CONF_FP2_BED): _entity("binary_sensor"),
                vol.Optional(CONF_FP2_DESK): _entity("binary_sensor"),
                vol.Required(CONF_DOOR_WITNESSES): _entity("binary_sensor", multiple=True),
                vol.Required(CONF_PHONE_TRACKER): _entity("device_tracker"),
                vol.Optional(CONF_PHONE_SSID): _entity("sensor"),
            }
        )
        schema = self.add_suggested_values_to_schema(schema, user_input or {})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> RoomPresenceOptionsFlow:
        return RoomPresenceOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_PLACE: PlaceSubentryFlow}


class RoomPresenceOptionsFlow(OptionsFlow):
    """Edit the optional sensors and every timing; the entry reloads on save."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=_drop_empty(user_input))
        current = {**DEFAULTS, **self.config_entry.options}
        fields: dict[Any, Any] = {
            vol.Optional(key, description={"suggested_value": current.get(key)}): selector
            for key, selector in SENSOR_FIELDS.items()
        }
        fields |= {
            vol.Optional(key, default=current[key]): selector
            for key, selector in OPTION_FIELDS.items()
        }
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))


class PlaceSubentryFlow(ConfigSubentryFlow):
    """Add or edit a place: a name, SSIDs and/or zones, match mode, priority and on-site flag."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._form("user", user_input, None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._form("reconfigure", user_input, self._get_reconfigure_subentry())

    async def _form(self, step_id: str, user_input: dict[str, Any] | None, existing: Any):
        errors: dict[str, str] = {}
        if user_input is not None:
            slug = slugify(user_input["name"])
            taken = {
                slugify(sub.data["name"])
                for sid, sub in self._get_entry().subentries.items()
                if sub.subentry_type == SUBENTRY_PLACE
                and (existing is None or sid != existing.subentry_id)
            }
            if not slug or slug in LOCATIONS_FIXED:
                errors["name"] = "place_name_reserved"
            elif slug in taken:
                errors["name"] = "place_name_taken"
            elif not user_input.get("ssids") and not user_input.get("zones"):
                errors["base"] = "place_needs_ssid_or_zone"
            else:
                data = {
                    "name": user_input["name"],
                    "ssids": list(user_input.get("ssids") or []),
                    "zones": list(user_input.get("zones") or []),
                    "mode": user_input.get("mode", "any"),
                    "priority": int(user_input.get("priority", 0)),
                    "on_site": bool(user_input.get("on_site", False)),
                }
                if existing is None:
                    return self.async_create_entry(title=data["name"], data=data)
                return self.async_update_and_abort(
                    self._get_entry(), existing, title=data["name"], data=data
                )
        defaults = dict(existing.data) if existing is not None else {}
        defaults |= user_input or {}
        schema = vol.Schema(
            {
                vol.Required("name"): TextSelector(),
                vol.Optional("ssids"): TextSelector(TextSelectorConfig(multiple=True)),
                vol.Optional("zones"): _entity("zone", multiple=True),
                vol.Required("mode", default="any"): SelectSelector(
                    SelectSelectorConfig(options=["any", "all"], translation_key="mode")
                ),
                vol.Required("priority", default=0): NumberSelector(
                    NumberSelectorConfig(min=0, max=100, step=1, mode=NumberSelectorMode.BOX)
                ),
                vol.Required("on_site", default=False): BooleanSelector(),
            }
        )
        schema = self.add_suggested_values_to_schema(schema, defaults)
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)
