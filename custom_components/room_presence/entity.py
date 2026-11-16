"""Shared base for room_presence entities, the person and room devices, and the fixed words.

Person entities live on a device named after the tracked person (identifier: the person
subentry id); room entities on one device per room subentry, placed in the room's area.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.translation import async_get_translations

from .const import CONF_AREA, CONF_PERSON, DOMAIN
from .coordinator import PresenceCoordinator

#: English for the fixed words, used when the active language has no translation for one.
WORDS_EN = {
    "home": "Home",
    "away": "Away",
    "unknown": "Unknown",
    "just_left": "Just left",
    "asleep": "asleep",
    "showering": "showering",
}


async def async_words(hass: HomeAssistant) -> dict[str, str]:
    """Return the fixed words (keys of :data:`WORDS_EN`) in HA's language, English where missing.

    Read from the ``state`` sensor's state translations; a language change applies on the next
    reload of the entry.
    """
    found = await async_get_translations(hass, hass.config.language, "entity", [DOMAIN])
    prefix = f"component.{DOMAIN}.entity.sensor.state.state."
    return {key: found.get(prefix + key, en) for key, en in WORDS_EN.items()}


def person_device(hass: HomeAssistant, sub: ConfigSubentry) -> DeviceInfo:
    """Device info for the person subentry ``sub``, named after the person's current name."""
    st = hass.states.get(sub.data.get(CONF_PERSON, ""))
    return DeviceInfo(
        identifiers={(DOMAIN, sub.subentry_id)},
        name=st.name if st is not None else sub.title,
        model="Person presence",
    )


def room_device(hass: HomeAssistant, entry_id: str, sub: ConfigSubentry) -> DeviceInfo:
    """Create or update the room subentry's device, put it in the room's area, return its info.

    The configured area wins on every setup; with no area configured the device's area is left
    as it is.
    """
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry_id,
        config_subentry_id=sub.subentry_id,
        identifiers={(DOMAIN, sub.subentry_id)},
        name=sub.title,
        model="Room",
    )
    area = sub.data.get(CONF_AREA)
    if area and device.area_id != area:
        devices.async_update_device(device.id, area_id=area)
    return DeviceInfo(identifiers={(DOMAIN, sub.subentry_id)})


class PresenceEntity(Entity):
    """An entity of the person or of one room; its state is re-read after every coordinator
    change."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coord: PresenceCoordinator,
        sub: ConfigSubentry,
        key: str,
        device: DeviceInfo,
        name: str | None = None,
    ) -> None:
        """:param coord: the entry's coordinator.
        :param sub: the person or room subentry this entity belongs to.
        :param key: the unique-id suffix (``<subentry_id>_<key>``), and the translation key
            unless ``name`` is given.
        :param device: the device the entity is attached to.
        :param name: a literal entity name (spots), instead of a translated one.
        """
        self.coord = coord
        self._attr_unique_id = f"{sub.subentry_id}_{key}"
        self._attr_device_info = device
        if name is None:
            self._attr_translation_key = key
        else:
            self._attr_name = name

    async def async_added_to_hass(self) -> None:
        """Rewrite the state whenever the coordinator changes anything."""
        self.async_on_remove(self.coord.add_listener(self.async_write_ha_state))
