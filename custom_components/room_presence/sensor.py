"""The ``location`` enum sensor: where the occupant is."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import PresenceConfigEntry
from .engine.machine import LOCATIONS_FIXED
from .entity import PresenceEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PresenceConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the location sensor."""
    async_add_entities([LocationSensor(entry.runtime_data, "location")])


class LocationSensor(PresenceEntity, SensorEntity):
    """The location: a fixed state (bed, desk, ..., away, unknown) or a configured place slug."""

    _attr_device_class = SensorDeviceClass.ENUM

    @property
    def options(self) -> list[str]:
        return [*LOCATIONS_FIXED, *(p.slug for p in self.coord.places)]

    @property
    def native_value(self) -> str:
        return self.coord.snapshot.location

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snap = self.coord.snapshot
        phone = self.coord.cfg.phone_tracker
        st = self.hass.states.get(phone) if phone else None
        age = (dt_util.utcnow() - st.last_changed).total_seconds() if st is not None else None
        return {
            "since": snap.since.isoformat() if snap.since else None,
            "room_state": snap.room_state.value,
            "held": snap.held,
            "place": snap.place,
            "conflict": snap.conflict,
            "reason": snap.reason,
            "phone_age": round(age) if age is not None else None,
        }
