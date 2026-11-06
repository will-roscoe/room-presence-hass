"""Shared base for room_presence entities: one device per tracked room."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import CONF_NAME, DOMAIN
from .coordinator import PresenceCoordinator


class PresenceEntity(Entity):
    """An entity of one tracked room; its state is re-read after every coordinator change."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coord: PresenceCoordinator, key: str) -> None:
        """:param key: the translation key, also the unique-id suffix (``<entry_id>_<key>``)."""
        entry = coord.entry
        self.coord = coord
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get(CONF_NAME, entry.title),
            model="Room presence",
        )

    async def async_added_to_hass(self) -> None:
        """Rewrite the state whenever the coordinator changes anything."""
        self.async_on_remove(self.coord.add_listener(self.async_write_ha_state))
