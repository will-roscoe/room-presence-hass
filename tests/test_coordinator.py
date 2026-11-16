"""The coordinator: live state changes in, snapshot, deadlines and bus events out."""

from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.room_presence.const import EVENT_TRANSITION
from custom_components.room_presence.discovery import person_ssid_sensors

from .conftest import BED, PERSON, ROOM, THP_DOOR, TRACKER, setup

pytestmark = pytest.mark.usefixtures("no_platforms")


async def advance(hass, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def set_state(hass, freezer, entity: str, state: str, after: float = 1, **attrs) -> None:
    freezer.tick(timedelta(seconds=after))
    hass.states.async_set(entity, state, attrs or None)
    await hass.async_block_till_done()


async def test_starts_home_from_current_states(hass, entry):
    await setup(hass, entry)
    snap = entry.runtime_data.snapshot
    assert snap.key == "home"
    assert snap.home is True


async def test_live_change_updates_snapshot(hass, entry, freezer):
    await setup(hass, entry)
    await set_state(hass, freezer, ROOM, "on")
    await set_state(hass, freezer, BED, "on")
    assert entry.runtime_data.snapshot.key == "bed"


async def test_deadlines_fire_exit_and_expiry(hass, entry, freezer):
    await setup(hass, entry)
    await set_state(hass, freezer, ROOM, "on")
    await set_state(hass, freezer, THP_DOOR, "on", after=30)
    await set_state(hass, freezer, ROOM, "off", after=20)
    coord = entry.runtime_data
    assert coord.snapshot.key == "bedroom"  # the exit waits for the move window
    await advance(hass, freezer, 30)
    assert coord.snapshot.key == "just_left"
    await advance(hass, freezer, 249)
    assert coord.snapshot.key == "just_left"
    await advance(hass, freezer, 2)
    assert coord.snapshot.key == "away"  # the phone said home only before the exit


async def test_transition_fires_bus_event(hass, entry, freezer):
    events = async_capture_events(hass, EVENT_TRANSITION)
    await setup(hass, entry)
    await set_state(hass, freezer, ROOM, "on")
    ev = events[-1].data
    assert (ev["entry_id"], ev["old"], ev["new"], ev["reason"]) == (
        entry.entry_id,
        "home",
        "bedroom",
        "entry",
    )
    assert "at" in ev


async def test_person_zone_name_mapped(hass, entry, freezer):
    await setup(hass, entry)
    await set_state(hass, freezer, PERSON, "Uni of Lancashire", device_trackers=[TRACKER])
    snap = entry.runtime_data.snapshot
    assert snap.key == "uni"
    assert snap.place == "uni"


async def test_transitions_and_edges_are_recorded(hass, entry, freezer):
    await setup(hass, entry)
    await set_state(hass, freezer, ROOM, "on")
    coord = entry.runtime_data
    assert coord.transitions[-1].new == "bedroom"
    assert coord.edges[-1].entity_id == ROOM


async def test_attribute_only_changes_skipped(hass, entry, freezer):
    await setup(hass, entry)
    coord = entry.runtime_data
    count = len(coord.edges)
    await set_state(hass, freezer, ROOM, "off", friendly_name="FP2 room")
    assert len(coord.edges) == count


async def test_unload_cancels_timers(hass, entry, freezer):
    events = async_capture_events(hass, EVENT_TRANSITION)
    await setup(hass, entry)
    await set_state(hass, freezer, ROOM, "on")
    await set_state(hass, freezer, THP_DOOR, "on", after=30)
    await set_state(hass, freezer, ROOM, "off", after=20)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    count = len(events)
    await advance(hass, freezer, 600)
    await set_state(hass, freezer, ROOM, "on")
    assert len(events) == count


async def test_ssid_sensors_found_from_trackers(hass):
    owner = MockConfigEntry(domain="mobile_app")
    owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("mobile_app", "phone")}
    )
    ents = er.async_get(hass)
    for domain, uid in (("device_tracker", "phone"), ("sensor", "phone_ssid"), ("sensor", "bat")):
        ents.async_get_or_create(
            domain, "mobile_app", uid, suggested_object_id=uid, device_id=device.id
        )
    hass.states.async_set("person.alex", "home", {"device_trackers": ["device_tracker.phone"]})
    assert person_ssid_sensors(hass, "person.alex") == ["sensor.phone_ssid"]
    hass.states.async_set("person.sam", "home")
    assert person_ssid_sensors(hass, "person.sam") == []
    assert person_ssid_sensors(hass, "person.nobody") == []
