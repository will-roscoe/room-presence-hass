"""Config flow placeholder while the home setup flow is rebuilt."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class RoomPresenceConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the home (rebuilt next)."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_abort(reason="not_ready")
