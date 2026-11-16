"""Find a person's Wi-Fi network (SSID) sensors from the devices of their trackers."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er


def person_ssid_sensors(hass: HomeAssistant, person_entity_id: str) -> list[str]:
    """Return the ``sensor.*_ssid`` entities on the devices of the person's device trackers.

    The trackers come from the person state's ``device_trackers`` attribute (e.g. the companion
    app's ``device_tracker.phone`` sits on a device that also has ``sensor.phone_ssid``). Returns a
    sorted list without duplicates; empty when the person, a tracker or its device is unknown.
    """
    state = hass.states.get(person_entity_id)
    if state is None:
        return []
    registry = er.async_get(hass)
    found: set[str] = set()
    for tracker in state.attributes.get("device_trackers") or ():
        entry = registry.async_get(tracker)
        if entry is None or entry.device_id is None:
            continue
        for other in er.async_entries_for_device(registry, entry.device_id):
            if other.domain == "sensor" and other.entity_id.endswith("_ssid"):
                found.add(other.entity_id)
    return sorted(found)
