"""The ``room_presence.set_state`` action: force the location, now or after a delay."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import DOMAIN
from .coordinator import PresenceCoordinator

ATTR_ENTRY = "config_entry_id"

SET_STATE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY): cv.string,
        vol.Required("state"): cv.string,
        vol.Optional("delay"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    }
)


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> PresenceCoordinator:
    """Return the targeted entry's coordinator; the id may be omitted when only one is loaded."""
    entries = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED
    ]
    wanted = call.data.get(ATTR_ENTRY)
    if wanted is not None:
        entries = [e for e in entries if e.entry_id == wanted]
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_found",
                translation_placeholders={"entry_id": wanted},
            )
    elif len(entries) != 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_required")
    return entries[0].runtime_data


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the integration's actions (once, for all entries)."""

    async def set_state(call: ServiceCall) -> None:
        coord = _coordinator(hass, call)
        try:
            await coord.async_set_state(call.data["state"], call.data.get("delay"))
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_state",
                translation_placeholders={"state": call.data["state"]},
            ) from err

    hass.services.async_register(DOMAIN, "set_state", set_state, schema=SET_STATE_SCHEMA)
