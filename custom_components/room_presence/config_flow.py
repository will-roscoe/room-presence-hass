"""Config flow for room_presence."""

from __future__ import annotations

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN


class RoomPresenceConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a tracked room."""

    VERSION = 1
