"""Upgrade a v0.1 entry (config entry version 1: one room, its presence zones and the phone) to the
home model (version 2: a person, rooms and places as subentries).

Call :func:`async_migrate` from ``async_migrate_entry``. It always leaves an entry that loads:
whatever it cannot resolve is left empty and raised as a repair issue by the setup checks.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import slugify

from .const import (
    CONF_AREA,
    CONF_BATHROOM,
    CONF_ENTRY_NEEDS_CROSSING,
    CONF_EXCLUSIVE,
    CONF_EXITS,
    CONF_HOME_SSIDS,
    CONF_HOME_ZONE,
    CONF_HUMIDITY,
    CONF_MOTION,
    CONF_NAME,
    CONF_PERSON,
    CONF_PRESENCE,
    CONF_REBUILD_LOOKBACK,
    CONF_ROLE,
    CONF_SENSOR,
    CONF_SLEEP_ENTITIES,
    CONF_SPOTS,
    CONF_SSID_SENSORS,
    CONF_STILLNESS,
    DOMAIN,
    ROLE_BED,
    SUBENTRY_PERSON,
    SUBENTRY_PLACE,
    SUBENTRY_ROOM,
    TIMING_KEYS,
    V1_BATHROOM,
    V1_BATHROOM_WINDOW,
    V1_DOOR_WITNESSES,
    V1_FALLBACK_MOTION,
    V1_FP2_BED,
    V1_FP2_DESK,
    V1_FP2_ROOM,
    V1_MOVEMENT,
    V1_ON_SITE,
    V1_PHONE_SSID,
    V1_PHONE_TRACKER,
    V1_SLEEP_ENTITIES,
)
from .discovery import person_ssid_sensors
from .engine.types import RESERVED_SLUGS

#: Options carried over unchanged from v0.1.
CARRIED_OPTIONS = (*TIMING_KEYS, CONF_REBUILD_LOOKBACK, CONF_ENTRY_NEEDS_CROSSING)


async def async_migrate(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Upgrade ``entry`` from version 1 to 2; ``True`` when it is (or already was) version 2.

    Writes the person, room and place subentries, the new data and options, removes the v0.1
    entities, device and stored corrections. Only the domain's first entry is upgraded: any
    other version 1 entry returns ``False`` and raises the ``multiple_entries`` issue.
    """
    if entry.version != 1:
        return entry.version == 2
    first = hass.config_entries.async_entries(DOMAIN)[0]
    if first.entry_id != entry.entry_id:
        ir.async_create_issue(
            hass,
            DOMAIN,
            "multiple_entries",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="multiple_entries",
            translation_placeholders={"title": entry.title},
        )
        return False
    conf = {**entry.data, **entry.options}
    places = [s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_PLACE]
    for sub in places:
        data = {k: v for k, v in sub.data.items() if k != V1_ON_SITE}
        hass.config_entries.async_update_subentry(entry, sub, data=data)
    if (person := _person(hass, conf.get(V1_PHONE_TRACKER))) is not None:
        _add(
            hass,
            entry,
            SUBENTRY_PERSON,
            _person_name(hass, person),
            _person_data(hass, person, conf),
            unique_id=person,
        )
    taken = {slugify(s.data[CONF_NAME]) for s in places}
    for room in _rooms(hass, entry.title, conf, taken):
        _add(hass, entry, SUBENTRY_ROOM, room[CONF_NAME], room)
    _remove_v01(hass, entry)
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.manual").async_remove()
    hass.config_entries.async_update_entry(
        entry,
        title="Home",
        data={CONF_HOME_ZONE: "zone.home", CONF_HOME_SSIDS: []},
        options=_options(entry.options),
        version=2,
    )
    return True


def _person(hass: HomeAssistant, tracker: str | None) -> str | None:
    """The only ``person`` whose ``device_trackers`` include ``tracker``; ``None`` if none or
    several do."""
    found = [
        st.entity_id
        for st in hass.states.async_all("person")
        if tracker and tracker in (st.attributes.get("device_trackers") or ())
    ]
    return found[0] if len(found) == 1 else None


def _person_name(hass: HomeAssistant, person: str) -> str:
    st = hass.states.get(person)
    return st.name if st is not None else person


def _person_data(hass: HomeAssistant, person: str, conf: dict[str, Any]) -> dict[str, Any]:
    sensors = person_ssid_sensors(hass, person)
    if (ssid := conf.get(V1_PHONE_SSID)) and ssid not in sensors:
        sensors.append(ssid)
    return {CONF_PERSON: person, CONF_SSID_SENSORS: sensors}


def _area(hass: HomeAssistant, entity_id: str) -> str | None:
    """The area of the device ``entity_id`` belongs to, ``None`` if it has none."""
    ent = er.async_get(hass).async_get(entity_id)
    if ent is None or ent.device_id is None:
        return None
    device = dr.async_get(hass).async_get(ent.device_id)
    return device.area_id if device is not None else None


def _unique(name: str, taken: set[str]) -> str:
    """``name``, or ``name 2``, ``name 3``... until its slug is free; claims the slug."""
    candidate, n = name, 1
    while slugify(candidate) in taken or slugify(candidate) in RESERVED_SLUGS:
        n += 1
        candidate = f"{name} {n}"
    taken.add(slugify(candidate))
    return candidate


def _spot(name: str, sensor: str, bed: bool = False, stillness=None, sleep=()) -> dict[str, Any]:
    return {
        CONF_NAME: name,
        CONF_SENSOR: sensor,
        CONF_ROLE: ROLE_BED if bed else None,
        CONF_STILLNESS: stillness,
        CONF_SLEEP_ENTITIES: list(sleep),
    }


def _rooms(
    hass: HomeAssistant, title: str, conf: dict[str, Any], taken: set[str]
) -> list[dict[str, Any]]:
    """The room subentries' data: the v0.1 room (if it had a room zone), then the bathroom."""
    out: list[dict[str, Any]] = []
    bathroom = conf.get(V1_BATHROOM)
    if sensor := conf.get(V1_FP2_ROOM):
        spots = []
        if bed := conf.get(V1_FP2_BED):
            spots.append(
                _spot("Bed", bed, True, conf.get(V1_MOVEMENT), conf.get(V1_SLEEP_ENTITIES) or ())
            )
        if desk := conf.get(V1_FP2_DESK):
            spots.append(_spot("Desk", desk))
        taken |= {slugify(s[CONF_NAME]) for s in spots}
        if slugify(title) in RESERVED_SLUGS or not slugify(title):
            title = "Room"
        motion = [e for e in conf.get(V1_FALLBACK_MOTION) or () if e != bathroom]
        out.append(
            _room(
                _unique(title, taken),
                _area(hass, sensor),
                [sensor],
                motion,
                list(conf.get(V1_DOOR_WITNESSES) or ()),
                spots,
            )
        )
    if bathroom:
        out.append(
            _room(
                _unique("Bathroom", taken),
                _area(hass, bathroom),
                [bathroom],
                [],
                [],
                [],
                bathroom=True,
            )
        )
    return out


def _room(name, area, presence, motion, exits, spots, bathroom: bool = False) -> dict[str, Any]:
    return {
        CONF_NAME: name,
        CONF_AREA: area,
        CONF_PRESENCE: presence,
        CONF_MOTION: motion,
        CONF_EXITS: exits,
        CONF_BATHROOM: bathroom,
        CONF_EXCLUSIVE: bathroom,
        CONF_HUMIDITY: None,
        CONF_SPOTS: spots,
    }


def _options(old: Any) -> dict[str, Any]:
    """v0.1 options to v0.2: timings and flags carried, ``bathroom_window`` → ``move_window``."""
    out = {k: v for k, v in old.items() if k in CARRIED_OPTIONS}
    if V1_BATHROOM_WINDOW in old:
        out["move_window"] = old[V1_BATHROOM_WINDOW]
    return out


def _add(hass, entry, kind: str, title: str, data: dict[str, Any], unique_id=None) -> None:
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=MappingProxyType(data), subentry_type=kind, title=title, unique_id=unique_id
        ),
    )


def _remove_v01(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the v0.1 entities and the v0.1 device (identifier: the entry id)."""
    ents = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(ents, entry.entry_id):
        ents.async_remove(ent.entity_id)
    devices = dr.async_get(hass)
    device = devices.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    if device is not None:
        devices.async_remove_device(device.id)
