"""The room_presence integration: where one person is, from an FP2, THP sensors and a phone."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import PresenceCoordinator
from .services import async_register_services

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

type PresenceConfigEntry = ConfigEntry[PresenceCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions shared by all entries."""
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PresenceConfigEntry) -> bool:
    """Set up one tracked room: rebuild its state, then follow its inputs live."""
    coord = PresenceCoordinator(hass, entry)
    entry.runtime_data = coord
    await coord.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: PresenceConfigEntry) -> None:
    """Reload after the options or the places change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PresenceConfigEntry) -> bool:
    """Unload the platforms and stop the coordinator."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_stop()
    return ok
