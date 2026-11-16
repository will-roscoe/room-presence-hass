"""Automatic upgrade of a v0.1 entry (version 1) to the v0.2 home model (version 2)."""

from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.util import slugify
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_presence.const import DOMAIN

from .conftest import BATH, BED, DESK, DOOR, HUM, PERSON, ROOM, SSID, THP_DOOR, THP_ROOM, TRACKER

THP_4 = "binary_sensor.tuya_thp_4_presence"
#: Will's live v0.1 entry (read from its diagnostics, 16 Nov).
V1_DATA = {
    "door_witnesses": [THP_DOOR, DOOR],
    "fp2_bed": BED,
    "fp2_desk": DESK,
    "fp2_room": ROOM,
    "name": "Room",
    "phone_tracker": TRACKER,
}
V1_OPTIONS = {
    "bathroom": BATH,
    "bathroom_window": 30.0,
    "bed_leave_grace": 20.0,
    "crossing_window": 60.0,
    "entry_needs_crossing": False,
    "entry_window": 90.0,
    "exit_door_on_time": 8.0,
    "exit_pre_window": 180.0,
    "exit_quiet": 180.0,
    "fallback_motion": [THP_ROOM, THP_DOOR, BATH, THP_4],
    "movement": THP_ROOM,
    "rebuild_lookback": 24.0,
    "recently_left_duration": 300.0,
    "sleep_quiet": 1800.0,
    "wake_on_time": 8.0,
}
TIMINGS = {
    k: v
    for k, v in V1_OPTIONS.items()
    if k not in ("bathroom", "bathroom_window", "fallback_motion", "movement")
}


def v1_entry(hass, title="Room", data=V1_DATA, options=V1_OPTIONS, places=()) -> MockConfigEntry:
    e = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        version=1,
        data=dict(data),
        options=dict(options),
        subentries_data=[
            ConfigSubentryData(
                data=p, subentry_type="place", title=p["name"], unique_id=slugify(p["name"])
            )
            for p in places
        ],
    )
    e.add_to_hass(hass)
    return e


@pytest.fixture
def phone_and_area(hass):
    """The FP2 room sensor on a device in an area, and the phone's tracker and SSID sensor on
    one device; registered before their states are set. Returns the area id."""
    devs, ents = dr.async_get(hass), er.async_get(hass)
    area = ar.async_get(hass).async_create("Upstairs")
    for domain in ("homekit_controller", "mobile_app"):
        MockConfigEntry(domain=domain, entry_id=domain).add_to_hass(hass)
    fp2 = devs.async_get_or_create(
        config_entry_id="homekit_controller", identifiers={("hk", "fp2")}
    )
    devs.async_update_device(fp2.id, area_id=area.id)
    ents.async_get_or_create(
        "binary_sensor",
        "homekit_controller",
        "fp2_room",
        suggested_object_id=ROOM.split(".")[1],
        device_id=fp2.id,
    )
    phone = devs.async_get_or_create(
        config_entry_id="mobile_app", identifiers={("mobile_app", "v")}
    )
    for domain, uid in (("device_tracker", TRACKER), ("sensor", SSID)):
        ents.async_get_or_create(
            domain, "mobile_app", uid, suggested_object_id=uid.split(".")[1], device_id=phone.id
        )
    return area.id


@pytest.fixture
def live_states(hass, phone_and_area):
    for e in (ROOM, BED, DESK, DOOR, THP_ROOM, BATH, THP_DOOR, THP_4):
        hass.states.async_set(e, "off")
    hass.states.async_set(HUM, "37")
    hass.states.async_set(PERSON, "home", {"device_trackers": [TRACKER], "friendly_name": "Will"})
    hass.states.async_set(SSID, "Not Connected")
    hass.states.async_set("zone.home", "0", {"friendly_name": "Home"})
    return phone_and_area


def subs(entry, kind: str) -> list:
    return [s for s in entry.subentries.values() if s.subentry_type == kind]


async def test_migrates_live_v01_entry(hass, live_states, hass_storage):
    area = live_states
    entry = v1_entry(hass)
    ents = er.async_get(hass)
    old = ents.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_location",
        config_entry=entry,
        suggested_object_id="room_location",
    )
    hass_storage[f"{DOMAIN}.{entry.entry_id}.manual"] = {"version": 1, "data": {"calls": []}}
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert (entry.version, entry.title) == (2, "Home")
    assert dict(entry.data) == {"home_zone": "zone.home", "home_ssids": []}
    assert dict(entry.options) == {**TIMINGS, "move_window": 30.0}
    [person] = subs(entry, "person")
    assert (person.title, person.unique_id) == ("Will", PERSON)
    assert dict(person.data) == {"person": PERSON, "ssid_sensors": [SSID]}
    rooms = {s.title: dict(s.data) for s in subs(entry, "room")}
    assert rooms == {
        "Room": {
            "name": "Room",
            "area_id": area,
            "presence": [ROOM],
            "motion": [THP_ROOM, THP_DOOR, THP_4],
            "exits": [THP_DOOR, DOOR],
            "bathroom": False,
            "exclusive": False,
            "humidity": None,
            "spots": [
                {
                    "name": "Bed",
                    "sensor": BED,
                    "role": "bed",
                    "stillness": THP_ROOM,
                    "sleep_entities": [],
                },
                {
                    "name": "Desk",
                    "sensor": DESK,
                    "role": None,
                    "stillness": None,
                    "sleep_entities": [],
                },
            ],
        },
        "Bathroom": {
            "name": "Bathroom",
            "area_id": None,
            "presence": [BATH],
            "motion": [],
            "exits": [],
            "bathroom": True,
            "exclusive": True,
            "humidity": None,
            "spots": [],
        },
    }
    assert ents.async_get(old.entity_id) is None
    assert f"{DOMAIN}.{entry.entry_id}.manual" not in hass_storage
    assert hass.states.get("sensor.will_location") is not None
    assert ir.async_get(hass).async_get_issue(DOMAIN, "person_missing") is None


async def test_migration_keeps_places_without_on_site(hass, live_states):
    place = {
        "name": "Uni",
        "ssids": ["eduroam"],
        "zones": [],
        "mode": "any",
        "priority": 0,
        "on_site": False,
    }
    entry = v1_entry(hass, places=(place,))
    assert await hass.config_entries.async_setup(entry.entry_id)
    [uni] = subs(entry, "place")
    assert dict(uni.data) == {k: v for k, v in place.items() if k != "on_site"}


@pytest.mark.parametrize(
    ("title", "room_name"), [("Home", "Room"), ("Bathroom", "Bathroom 2"), ("Bed", "Bed 2")]
)
async def test_migration_avoids_unloadable_names(hass, live_states, title, room_name):
    entry = v1_entry(hass, title=title, data={**V1_DATA, "name": title})
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert {s.title for s in subs(entry, "room")} == {room_name, "Bathroom"}


@pytest.mark.parametrize("several", [False, True], ids=["no_match", "two_matches"])
async def test_migration_without_matching_person_raises_issue(hass, live_states, several):
    if several:
        hass.states.async_set("person.alex", "home", {"device_trackers": [TRACKER]})
    else:
        hass.states.async_set(PERSON, "home", {"device_trackers": ["device_tracker.other"]})
    entry = v1_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 2
    assert subs(entry, "person") == []
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "person_missing")
    assert issue is not None and issue.is_fixable


async def test_migration_two_v1_entries(hass, live_states):
    first, second = v1_entry(hass), v1_entry(hass, title="Other")
    await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()
    assert (first.version, first.state) == (2, ConfigEntryState.LOADED)
    assert (second.version, second.state) == (1, ConfigEntryState.MIGRATION_ERROR)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "multiple_entries")
    assert issue is not None and not issue.is_fixable
