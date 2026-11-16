"""The person's ``location``, ``activity`` and ``state`` sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import PresenceConfigEntry
from .coordinator import PresenceCoordinator, person_subentry
from .engine.types import Activity, Presence, Snapshot
from .entity import PresenceEntity, async_words, person_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PresenceConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the person's three sensors (none if the entry has no person)."""
    sub = person_subentry(entry)
    if sub is None:
        return
    coord, words, device = entry.runtime_data, await async_words(hass), person_device(hass, sub)
    async_add_entities(
        [
            LocationSensor(coord, sub, "location", device, words),
            ActivitySensor(coord, sub, "activity", device),
            StateSensor(coord, sub, "state", device, words),
        ],
        config_subentry_id=sub.subentry_id,
    )


def location_text(snap: Snapshot, words: dict[str, str]) -> str:
    """The location's name: the spot, room, place or zone name, else the fixed word."""
    return snap.location_name or words.get(snap.location, snap.location)


def state_text(snap: Snapshot, words: dict[str, str]) -> str:
    """The display state: the location, ``(asleep)``/``(showering)`` appended, or ``Just left``."""
    if snap.presence is Presence.JUST_LEFT:
        return words["just_left"]
    text = location_text(snap, words)
    if snap.activity in (Activity.ASLEEP, Activity.SHOWERING):
        return f"{text} ({words[snap.activity.value]})"
    return text


class _WordsSensor(PresenceEntity, SensorEntity):
    def __init__(self, coord: PresenceCoordinator, sub, key, device, words) -> None:
        super().__init__(coord, sub, key, device)
        self._words = words


class LocationSensor(_WordsSensor):
    """The most specific location known, by name: spot, room, Home, place or zone, Away, Unknown.

    Attributes: ``since``, ``room``, ``spot``, ``held``, ``place``, ``conflict``, ``reason`` and
    ``phone_age`` (seconds since the person or one of their SSID sensors last changed).
    """

    _unrecorded_attributes = frozenset({"since", "phone_age", "reason"})

    @property
    def native_value(self) -> str:
        return location_text(self.coord.snapshot, self._words)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snap = self.coord.snapshot
        return {
            "since": snap.since.isoformat() if snap.since else None,
            "room": snap.room,
            "spot": snap.spot,
            "held": snap.held,
            "place": snap.place,
            "conflict": snap.conflict,
            "reason": snap.reason,
            "phone_age": self._phone_age(),
        }

    def _phone_age(self) -> int | None:
        home = self.coord.home
        changed = [
            st.last_changed
            for e in (home.person, *home.ssid_sensors)
            if e and (st := self.hass.states.get(e)) is not None
        ]
        if not changed:
            return None
        return round((dt_util.utcnow() - max(changed)).total_seconds())


class ActivitySensor(PresenceEntity, SensorEntity):
    """What the person is doing: asleep, showering, awake, away or unknown (translated)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [a.value for a in Activity]

    @property
    def native_value(self) -> str:
        return self.coord.snapshot.activity.value


class StateSensor(_WordsSensor):
    """The display state, e.g. ``Bed (asleep)``, ``Desk``, ``Just left``, ``Away``."""

    @property
    def native_value(self) -> str:
        return state_text(self.coord.snapshot, self._words)
