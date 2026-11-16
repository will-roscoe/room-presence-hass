"""The ``room_presence.set_state`` action: force the location and/or activity, now or later."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import DOMAIN
from .coordinator import PresenceCoordinator

ATTR_LOCATION = "location"
ATTR_ACTIVITY = "activity"
ATTR_DELAY = "delay"

SET_STATE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_LOCATION): cv.string,
        vol.Optional(ATTR_ACTIVITY): cv.string,
        vol.Optional(ATTR_DELAY): vol.All(vol.Coerce(float), vol.Range(min=0)),
    }
)


def _coordinator(hass: HomeAssistant) -> PresenceCoordinator:
    """Return the loaded entry's coordinator (there is at most one entry)."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")


def _check(coord: PresenceCoordinator, location: str | None, activity: str | None) -> None:
    """Raise a translated :class:`ServiceValidationError` for an invalid call."""
    if location is None and activity is None:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="nothing_to_set")
    for value, key, args in (
        (location, "invalid_location", (location, None)),
        (activity, "invalid_activity", (None, activity)),
    ):
        if value is None:
            continue
        try:
            coord.machine.check_manual(*args)
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key=key,
                translation_placeholders={"value": value},
            ) from err


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""

    async def set_state(call: ServiceCall) -> None:
        coord = _coordinator(hass)
        location, activity = call.data.get(ATTR_LOCATION), call.data.get(ATTR_ACTIVITY)
        _check(coord, location, activity)
        await coord.async_set_state(location, activity, call.data.get(ATTR_DELAY))

    hass.services.async_register(DOMAIN, "set_state", set_state, schema=SET_STATE_SCHEMA)
