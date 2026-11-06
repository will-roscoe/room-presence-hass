"""Convenience binary sensors derived from the location, and the degraded flag."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PresenceConfigEntry
from .coordinator import PresenceCoordinator
from .entity import PresenceEntity

IN_ROOM = frozenset({"bed", "desk", "room", "bathroom", "sleeping"})


def _home(coord: PresenceCoordinator, location: str) -> bool:
    """In the room, just left it, or at an on-site place."""
    if location in IN_ROOM or location == "recently_left":
        return True
    return any(p.slug == location and p.on_site for p in coord.places)


#: Binary sensor key → rule on (coordinator, location).
RULES: dict[str, Callable[[PresenceCoordinator, str], bool]] = {
    "in_room": lambda c, loc: loc in IN_ROOM,
    "in_bed": lambda c, loc: loc in ("bed", "sleeping"),
    "at_desk": lambda c, loc: loc == "desk",
    "in_bathroom": lambda c, loc: loc == "bathroom",
    "sleeping": lambda c, loc: loc == "sleeping",
    "recently_left": lambda c, loc: loc == "recently_left",
    "home": _home,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PresenceConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one binary sensor per rule, and the degraded flag."""
    coord = entry.runtime_data
    entities: list[BinarySensorEntity] = [LocationBinarySensor(coord, key) for key in RULES]
    entities.append(DegradedBinarySensor(coord, "degraded"))
    async_add_entities(entities)


class LocationBinarySensor(PresenceEntity, BinarySensorEntity):
    """On while the location satisfies this sensor's rule (see :data:`RULES`)."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    @property
    def is_on(self) -> bool:
        return RULES[self._attr_translation_key](self.coord, self.coord.snapshot.location)


class DegradedBinarySensor(PresenceEntity, BinarySensorEntity):
    """On while the FP2 room zone is unavailable and the THP fallback is in charge."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool:
        return self.coord.snapshot.degraded

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {"entity": self.coord.cfg.fp2_room if self.coord.snapshot.degraded else None}
