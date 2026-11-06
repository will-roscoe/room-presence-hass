"""The coordinator: live state changes in, snapshot, deadlines and bus events out."""

from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.room_presence.const import EVENT_TRANSITION

from .conftest import BED, ROOM, SSID, THP_DOOR, TRACKER, setup


async def advance(hass, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def set_state(hass, freezer, entity: str, state: str, after: float = 1) -> None:
    freezer.tick(timedelta(seconds=after))
    hass.states.async_set(entity, state)
    await hass.async_block_till_done()


async def test_starts_out_from_current_states(hass, entry):
    await setup(hass, entry)
    assert entry.runtime_data.snapshot.location == "away"


async def test_live_zone_change_updates_snapshot(hass, entry, freezer):
    await setup(hass, entry)
    await set_state(hass, freezer, ROOM, "on")
    await set_state(hass, freezer, BED, "on")
    assert entry.runtime_data.snapshot.location == "bed"


async def test_deadline_fires_recently_left_expiry(hass, entry, freezer):
    await setup(hass, entry)
    await set_state(hass, freezer, ROOM, "on")
    await set_state(hass, freezer, THP_DOOR, "on", after=30)
    await set_state(hass, freezer, ROOM, "off", after=20)
    coord = entry.runtime_data
    assert coord.snapshot.location == "recently_left"
    await advance(hass, freezer, 279)
    assert coord.snapshot.location == "recently_left"
    await advance(hass, freezer, 2)
    assert coord.snapshot.location == "away"


async def test_transition_fires_bus_event(hass, entry, freezer):
    events = async_capture_events(hass, EVENT_TRANSITION)
    await setup(hass, entry)
    await set_state(hass, freezer, ROOM, "on")
    ev = events[-1].data
    assert (ev["entry_id"], ev["old"], ev["new"], ev["reason"]) == (
        entry.entry_id,
        "away",
        "room",
        "entry",
    )
    assert "at" in ev


async def test_tracker_zone_name_maps_to_zone_entity(hass, entry, freezer):
    await setup(hass, entry)
    await set_state(hass, freezer, TRACKER, "Uni of Lancashire")
    assert entry.runtime_data.snapshot.location == "uni"
    await set_state(hass, freezer, SSID, "Not Connected")
    assert entry.runtime_data.snapshot.place == "uni"


async def test_transitions_and_edges_are_recorded(hass, entry, freezer):
    await setup(hass, entry)
    await set_state(hass, freezer, ROOM, "on")
    coord = entry.runtime_data
    assert coord.transitions[-1].new == "room"
    assert coord.edges[-1].entity_id == ROOM


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
