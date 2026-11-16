"""Config flow (the home and its person), options (timings), and the person, room and place
subentry flows. A room's spots are edited inside the room's own flow."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    AreaSelector,
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
    CONF_AREA,
    CONF_BATHROOM,
    CONF_ENTRY_NEEDS_CROSSING,
    CONF_EXCLUSIVE,
    CONF_EXITS,
    CONF_HOME_SSIDS,
    CONF_HOME_ZONE,
    CONF_HUMIDITY,
    CONF_MODE,
    CONF_MOTION,
    CONF_NAME,
    CONF_PERSON,
    CONF_PRESENCE,
    CONF_PRIORITY,
    CONF_REBUILD_LOOKBACK,
    CONF_ROLE,
    CONF_SENSOR,
    CONF_SHOWER_RISE,
    CONF_SLEEP_ENTITIES,
    CONF_SPOTS,
    CONF_SSID_SENSORS,
    CONF_SSIDS,
    CONF_STILLNESS,
    CONF_ZONES,
    DEFAULTS,
    DOMAIN,
    ROLE_BED,
    SUBENTRY_PERSON,
    SUBENTRY_PLACE,
    SUBENTRY_ROOM,
    TIMING_KEYS,
)
from .discovery import person_ssid_sensors
from .engine.types import RESERVED_SLUGS

CONF_SPOT = "spot"
ROLE_NONE = "none"


def _number(minimum: float, maximum: float, unit: str, step: float = 1) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=NumberSelectorMode.BOX,
        )
    )


def _entity(domain: str | None = None, multiple: bool = False, **extra: Any) -> EntitySelector:
    config = EntitySelectorConfig(multiple=multiple, **extra)
    if domain:
        config["domain"] = domain
    return EntitySelector(config)


#: Largest value offered for each timing (seconds).
_TIMING_MAX = {
    "crossing_window": 600,
    "move_window": 300,
    "recently_left_duration": 3600,
    "sleep_quiet": 14400,
    "wake_on_time": 120,
    "bed_leave_grace": 600,
    "exit_pre_window": 1800,
    "exit_quiet": 1800,
    "exit_door_on_time": 120,
    "entry_window": 600,
    "exclusive_quiet": 1800,
    "shower_window": 3600,
    "shower_end": 1800,
}
OPTION_FIELDS: dict[str, Any] = {key: _number(1, _TIMING_MAX[key], "s") for key in TIMING_KEYS}
OPTION_FIELDS |= {
    CONF_SHOWER_RISE: _number(1, 50, "%", step=0.5),
    CONF_REBUILD_LOOKBACK: _number(1, 240, "h"),
    CONF_ENTRY_NEEDS_CROSSING: BooleanSelector(),
}


def _person_name(hass: HomeAssistant, person: str) -> str:
    state = hass.states.get(person)
    return state.name if state is not None else person


def _taken_slugs(entry: ConfigEntry, exclude: str | None = None) -> set[str]:
    """Slugs used by the entry's rooms, their spots and its places (except subentry ``exclude``)."""
    out: set[str] = set()
    for subentry_id, sub in entry.subentries.items():
        if subentry_id == exclude:
            continue
        if sub.subentry_type == SUBENTRY_ROOM:
            out.add(slugify(sub.data[CONF_NAME]))
            out |= {slugify(s[CONF_NAME]) for s in sub.data.get(CONF_SPOTS) or ()}
        elif sub.subentry_type == SUBENTRY_PLACE:
            out.add(slugify(sub.data[CONF_NAME]))
    return out


def _person_schema(person: str | None, sensors: list[str]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_PERSON, default=person or vol.UNDEFINED): _entity("person"),
            vol.Optional(CONF_SSID_SENSORS, description={"suggested_value": sensors}): _entity(
                "sensor", multiple=True
            ),
        }
    )


class RoomPresenceConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the home: the person, the home zone and the home Wi-Fi networks."""

    VERSION = 2

    def __init__(self) -> None:
        self._home: dict[str, Any] = {}
        self._person = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._person = user_input[CONF_PERSON]
            self._home = {
                CONF_HOME_ZONE: user_input.get(CONF_HOME_ZONE) or "zone.home",
                CONF_HOME_SSIDS: list(user_input.get(CONF_HOME_SSIDS) or ()),
            }
            return await self.async_step_ssids()
        schema = vol.Schema(
            {
                vol.Required(CONF_PERSON): _entity("person"),
                vol.Required(CONF_HOME_ZONE, default="zone.home"): _entity("zone"),
                vol.Optional(CONF_HOME_SSIDS): TextSelector(TextSelectorConfig(multiple=True)),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_ssids(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            person = ConfigSubentryData(
                subentry_type=SUBENTRY_PERSON,
                data={
                    CONF_PERSON: self._person,
                    CONF_SSID_SENSORS: list(user_input.get(CONF_SSID_SENSORS) or ()),
                },
                title=_person_name(self.hass, self._person),
                unique_id=self._person,
            )
            return self.async_create_entry(title="Home", data=self._home, subentries=[person])
        schema = vol.Schema({vol.Optional(CONF_SSID_SENSORS): _entity("sensor", multiple=True)})
        found = person_ssid_sensors(self.hass, self._person)
        schema = self.add_suggested_values_to_schema(schema, {CONF_SSID_SENSORS: found})
        return self.async_show_form(step_id="ssids", data_schema=schema)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            data = {
                CONF_HOME_ZONE: user_input.get(CONF_HOME_ZONE) or "zone.home",
                CONF_HOME_SSIDS: list(user_input.get(CONF_HOME_SSIDS) or ()),
            }
            return self.async_update_reload_and_abort(entry, data=data)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOME_ZONE, default=entry.data.get(CONF_HOME_ZONE, "zone.home")
                ): _entity("zone"),
                vol.Optional(CONF_HOME_SSIDS): TextSelector(TextSelectorConfig(multiple=True)),
            }
        )
        schema = self.add_suggested_values_to_schema(
            schema, {CONF_HOME_SSIDS: entry.data.get(CONF_HOME_SSIDS) or []}
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> RoomPresenceOptionsFlow:
        return RoomPresenceOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {
            SUBENTRY_PERSON: PersonSubentryFlow,
            SUBENTRY_ROOM: RoomSubentryFlow,
            SUBENTRY_PLACE: PlaceSubentryFlow,
        }


class RoomPresenceOptionsFlow(OptionsFlow):
    """Every timing, the shower threshold, the rebuild look-back and the entry flag."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = {**DEFAULTS, **self.config_entry.options}
        fields = {
            vol.Optional(key, default=current[key]): selector
            for key, selector in OPTION_FIELDS.items()
        }
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))


class PersonSubentryFlow(ConfigSubentryFlow):
    """The tracked person: change who it is and their SSID sensors (one person for now)."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_abort(reason="single_person")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            person = user_input[CONF_PERSON]
            data = {
                CONF_PERSON: person,
                CONF_SSID_SENSORS: list(user_input.get(CONF_SSID_SENSORS) or ()),
            }
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                title=_person_name(self.hass, person),
                data=data,
                unique_id=person,
            )
        schema = _person_schema(
            subentry.data.get(CONF_PERSON),
            list(subentry.data.get(CONF_SSID_SENSORS) or ()),
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema)


class RoomSubentryFlow(ConfigSubentryFlow):
    """A room: its sensors, then a menu to add, edit, remove and reorder its spots."""

    def __init__(self) -> None:
        self._room: dict[str, Any] = {}
        self._spots: list[dict[str, Any]] = []
        self._editing: int | None = None
        self._spot: dict[str, Any] = {}
        self._loaded = False

    @property
    def _reconfiguring(self) -> bool:
        return self.source == SOURCE_RECONFIGURE

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._room_form("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if not self._loaded:
            data = self._get_reconfigure_subentry().data
            self._room = {k: v for k, v in data.items() if k != CONF_SPOTS}
            self._spots = [dict(s) for s in data.get(CONF_SPOTS) or ()]
            self._loaded = True
        return await self._room_form("reconfigure", user_input)

    def _taken(self) -> set[str]:
        exclude = self._reconfigure_subentry_id if self._reconfiguring else None
        return _taken_slugs(self._get_entry(), exclude)

    async def _room_form(self, step_id: str, user_input: dict[str, Any] | None):
        errors: dict[str, str] = {}
        if user_input is not None:
            slug = slugify(user_input[CONF_NAME])
            spot_slugs = {slugify(s[CONF_NAME]) for s in self._spots}
            if not slug or slug in RESERVED_SLUGS:
                errors[CONF_NAME] = "name_reserved"
            elif slug in self._taken() or slug in spot_slugs:
                errors[CONF_NAME] = "name_taken"
            elif not user_input.get(CONF_PRESENCE):
                errors[CONF_PRESENCE] = "presence_required"
            else:
                bathroom = bool(user_input.get(CONF_BATHROOM, False))
                self._room = {
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_AREA: user_input.get(CONF_AREA) or None,
                    CONF_PRESENCE: list(user_input[CONF_PRESENCE]),
                    CONF_MOTION: list(user_input.get(CONF_MOTION) or ()),
                    CONF_EXITS: list(user_input.get(CONF_EXITS) or ()),
                    CONF_BATHROOM: bathroom,
                    CONF_EXCLUSIVE: bool(user_input.get(CONF_EXCLUSIVE, False)),
                    CONF_HUMIDITY: self._room.get(CONF_HUMIDITY) if bathroom else None,
                }
                if bathroom:
                    return await self.async_step_humidity()
                return await self.async_step_spots()
        defaults = {**self._room, **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): TextSelector(),
                vol.Optional(CONF_AREA): AreaSelector(),
                vol.Required(CONF_PRESENCE): _entity("binary_sensor", multiple=True),
                vol.Optional(CONF_MOTION): _entity("binary_sensor", multiple=True),
                vol.Optional(CONF_EXITS): _entity("binary_sensor", multiple=True),
                vol.Required(CONF_BATHROOM, default=False): BooleanSelector(),
                vol.Required(CONF_EXCLUSIVE, default=False): BooleanSelector(),
            }
        )
        schema = self.add_suggested_values_to_schema(schema, defaults)
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

    async def async_step_humidity(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            self._room[CONF_HUMIDITY] = user_input.get(CONF_HUMIDITY) or None
            return await self.async_step_spots()
        schema = vol.Schema(
            {vol.Optional(CONF_HUMIDITY): _entity("sensor", device_class="humidity")}
        )
        schema = self.add_suggested_values_to_schema(
            schema, {CONF_HUMIDITY: self._room.get(CONF_HUMIDITY)}
        )
        return self.async_show_form(step_id="humidity", data_schema=schema)

    async def async_step_spots(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        options = ["add_spot"]
        if self._spots:
            options += ["edit_spot", "remove_spot"]
        if len(self._spots) > 1:
            options.append("move_spot_up")
        options.append("finish")
        names = ", ".join(s[CONF_NAME] for s in self._spots) or "-"
        return self.async_show_menu(
            step_id="spots", menu_options=options, description_placeholders={"spots": names}
        )

    async def async_step_add_spot(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self._editing = None
        return await self.async_step_spot()

    def _choose(self, step_id: str, names: list[str]) -> SubentryFlowResult:
        schema = vol.Schema(
            {vol.Required(CONF_SPOT): SelectSelector(SelectSelectorConfig(options=names))}
        )
        return self.async_show_form(step_id=step_id, data_schema=schema)

    def _index(self, name: str) -> int:
        return next(i for i, s in enumerate(self._spots) if s[CONF_NAME] == name)

    async def async_step_edit_spot(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            self._editing = self._index(user_input[CONF_SPOT])
            return await self.async_step_spot()
        return self._choose("edit_spot", [s[CONF_NAME] for s in self._spots])

    async def async_step_remove_spot(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            del self._spots[self._index(user_input[CONF_SPOT])]
            return await self.async_step_spots()
        return self._choose("remove_spot", [s[CONF_NAME] for s in self._spots])

    async def async_step_move_spot_up(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            i = self._index(user_input[CONF_SPOT])
            self._spots[i - 1], self._spots[i] = self._spots[i], self._spots[i - 1]
            return await self.async_step_spots()
        return self._choose("move_spot_up", [s[CONF_NAME] for s in self._spots[1:]])

    async def async_step_spot(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        current = self._spots[self._editing] if self._editing is not None else {}
        if user_input is not None:
            slug = slugify(user_input[CONF_NAME])
            others = {
                slugify(s[CONF_NAME]) for i, s in enumerate(self._spots) if i != self._editing
            }
            if not slug or slug in RESERVED_SLUGS:
                errors[CONF_NAME] = "name_reserved"
            elif slug in others:
                errors[CONF_NAME] = "spot_name_taken"
            elif slug in self._taken() or slug == slugify(self._room.get(CONF_NAME, "")):
                errors[CONF_NAME] = "name_taken"
            else:
                role = user_input.get(CONF_ROLE, ROLE_NONE)
                self._spot = {
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_SENSOR: user_input[CONF_SENSOR],
                    CONF_ROLE: ROLE_BED if role == ROLE_BED else None,
                    CONF_STILLNESS: current.get(CONF_STILLNESS),
                    CONF_SLEEP_ENTITIES: list(current.get(CONF_SLEEP_ENTITIES) or ()),
                }
                if self._spot[CONF_ROLE] == ROLE_BED:
                    return await self.async_step_spot_bed()
                self._spot[CONF_STILLNESS], self._spot[CONF_SLEEP_ENTITIES] = None, []
                return await self._save_spot()
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): TextSelector(),
                vol.Required(CONF_SENSOR): _entity("binary_sensor"),
                vol.Required(CONF_ROLE, default=ROLE_NONE): SelectSelector(
                    SelectSelectorConfig(options=[ROLE_NONE, ROLE_BED], translation_key="role")
                ),
            }
        )
        defaults = {**current, CONF_ROLE: current.get(CONF_ROLE) or ROLE_NONE}
        schema = self.add_suggested_values_to_schema(schema, {**defaults, **(user_input or {})})
        return self.async_show_form(step_id="spot", data_schema=schema, errors=errors)

    async def async_step_spot_bed(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            self._spot[CONF_STILLNESS] = user_input.get(CONF_STILLNESS) or None
            self._spot[CONF_SLEEP_ENTITIES] = list(user_input.get(CONF_SLEEP_ENTITIES) or ())
            return await self._save_spot()
        schema = vol.Schema(
            {
                vol.Optional(CONF_STILLNESS): _entity("binary_sensor"),
                vol.Optional(CONF_SLEEP_ENTITIES): _entity(multiple=True),
            }
        )
        schema = self.add_suggested_values_to_schema(schema, self._spot)
        return self.async_show_form(step_id="spot_bed", data_schema=schema)

    async def _save_spot(self) -> SubentryFlowResult:
        if self._editing is None:
            self._spots.append(self._spot)
        else:
            self._spots[self._editing] = self._spot
        self._editing, self._spot = None, {}
        return await self.async_step_spots()

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        data = {**self._room, CONF_SPOTS: self._spots}
        title = data[CONF_NAME]
        if self._reconfiguring:
            return self.async_update_and_abort(
                self._get_entry(), self._get_reconfigure_subentry(), title=title, data=data
            )
        return self.async_create_entry(title=title, data=data)


class PlaceSubentryFlow(ConfigSubentryFlow):
    """A place outside the home: a name, Wi-Fi networks and/or zones, match mode and priority."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._form("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._form("reconfigure", user_input)

    async def _form(self, step_id: str, user_input: dict[str, Any] | None):
        errors: dict[str, str] = {}
        existing = self._get_reconfigure_subentry() if step_id == "reconfigure" else None
        if user_input is not None:
            slug = slugify(user_input[CONF_NAME])
            taken = _taken_slugs(
                self._get_entry(), existing.subentry_id if existing is not None else None
            )
            if not slug or slug in RESERVED_SLUGS:
                errors[CONF_NAME] = "name_reserved"
            elif slug in taken:
                errors[CONF_NAME] = "name_taken"
            elif not user_input.get(CONF_SSIDS) and not user_input.get(CONF_ZONES):
                errors["base"] = "place_needs_ssid_or_zone"
            else:
                data = {
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_SSIDS: list(user_input.get(CONF_SSIDS) or ()),
                    CONF_ZONES: list(user_input.get(CONF_ZONES) or ()),
                    CONF_MODE: user_input.get(CONF_MODE, "any"),
                    CONF_PRIORITY: int(user_input.get(CONF_PRIORITY, 0)),
                }
                if existing is None:
                    return self.async_create_entry(title=data[CONF_NAME], data=data)
                return self.async_update_and_abort(
                    self._get_entry(), existing, title=data[CONF_NAME], data=data
                )
        defaults = dict(existing.data) if existing is not None else {}
        defaults |= user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): TextSelector(),
                vol.Optional(CONF_SSIDS): TextSelector(TextSelectorConfig(multiple=True)),
                vol.Optional(CONF_ZONES): _entity("zone", multiple=True),
                vol.Required(CONF_MODE, default="any"): SelectSelector(
                    SelectSelectorConfig(options=["any", "all"], translation_key="mode")
                ),
                vol.Required(CONF_PRIORITY, default=0): NumberSelector(
                    NumberSelectorConfig(min=0, max=100, step=1, mode=NumberSelectorMode.BOX)
                ),
            }
        )
        schema = self.add_suggested_values_to_schema(schema, defaults)
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)
