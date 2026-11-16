"""Binary sensors: the person's ``home``, ``just_left``, ``asleep`` and ``showering``, and each
room's ``occupied``, one per spot, and ``degraded``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PresenceConfigEntry
from .const import SUBENTRY_ROOM
from .coordinator import PresenceCoordinator, person_subentry, room_config
from .engine.rooms import UNSEEN
from .engine.types import Presence, RoomConfig, Snapshot
from .entity import PresenceEntity, person_device, room_device

#: Person binary sensor key → rule on the snapshot.
PERSON_RULES: dict[str, Callable[[Snapshot], bool]] = {
    "home": lambda s: s.home,
    "just_left": lambda s: s.presence is Presence.JUST_LEFT,
    "asleep": lambda s: s.asleep,
    "showering": lambda s: s.showering,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PresenceConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the person's binary sensors, then each room's on its own device."""
    coord = entry.runtime_data
    if (person := person_subentry(entry)) is not None:
        device = person_device(hass, person)
        async_add_entities(
            [PersonBinarySensor(coord, person, key, device) for key in PERSON_RULES],
            config_subentry_id=person.subentry_id,
        )
    for sub in entry.subentries.values():
        if sub.subentry_type != SUBENTRY_ROOM:
            continue
        room = room_config(sub.data)
        device = room_device(hass, entry.entry_id, sub)
        entities: list[BinarySensorEntity] = [
            OccupiedBinarySensor(coord, sub, "occupied", device, room)
        ]
        entities += [
            SpotBinarySensor(coord, sub, f"spot_{spot.slug}", device, room, spot.slug, spot.name)
            for spot in room.spots
        ]
        entities.append(DegradedBinarySensor(coord, sub, "degraded", device, room))
        async_add_entities(entities, config_subentry_id=sub.subentry_id)


class PersonBinarySensor(PresenceEntity, BinarySensorEntity):
    """On while the person's rule holds (see :data:`PERSON_RULES`); ``home`` is an occupancy
    sensor."""

    def __init__(self, coord: PresenceCoordinator, sub, key: str, device: DeviceInfo) -> None:
        super().__init__(coord, sub, key, device)
        self._rule = PERSON_RULES[key]
        if key == "home":
            self._attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    @property
    def is_on(self) -> bool:
        return self._rule(self.coord.snapshot)


class _RoomBinarySensor(PresenceEntity, BinarySensorEntity):
    def __init__(
        self,
        coord: PresenceCoordinator,
        sub: ConfigSubentry,
        key: str,
        device: DeviceInfo,
        room: RoomConfig,
        name: str | None = None,
    ) -> None:
        super().__init__(coord, sub, key, device, name)
        self.room = room


class OccupiedBinarySensor(_RoomBinarySensor):
    """On while the person is in this room, held there included."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    @property
    def is_on(self) -> bool:
        snap = self.coord.snapshot
        return snap.presence is Presence.IN_ROOM and snap.room == self.room.slug


class SpotBinarySensor(_RoomBinarySensor):
    """On while the person is at this spot of the room."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, coord, sub, key, device, room, spot: str, name: str) -> None:
        super().__init__(coord, sub, key, device, room, name)
        self.spot = spot

    @property
    def is_on(self) -> bool:
        snap = self.coord.snapshot
        return (
            snap.presence is Presence.IN_ROOM
            and snap.room == self.room.slug
            and snap.spot == self.spot
        )


class DegradedBinarySensor(_RoomBinarySensor):
    """On while every presence sensor of the room is unavailable (the fallback is in charge).

    Attribute ``entities``: the room's presence sensors that are unavailable or unknown now.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool:
        return self.room.slug in self.coord.snapshot.degraded

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sensors = self.coord.machine.rooms[self.room.slug]
        return {
            "entities": [e for e in self.room.presence if sensors.state_of(e) in (None, *UNSEEN)]
        }
