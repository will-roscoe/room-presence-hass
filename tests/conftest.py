"""Shared fixtures: a home entry (version 2) on Will's live entity ids, and its input states."""

from homeassistant.config_entries import ConfigSubentryData
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_presence.const import DOMAIN

ROOM = "binary_sensor.aqara_fp2_room_presence"
BED = "binary_sensor.aqara_fp2_bed_presence"
DESK = "binary_sensor.aqara_fp2_desk_presence"
DOOR = "binary_sensor.aqara_fp2_door_presence"
THP_ROOM = "binary_sensor.tuya_thp_1_presence"
BATH = "binary_sensor.tuya_thp_2_presence"
THP_DOOR = "binary_sensor.tuya_thp_3_presence"
HUM = "sensor.tuya_th_4_humidity"
PERSON = "person.will"
TRACKER = "device_tracker.vega"
SSID = "sensor.pollux_ssid"
INPUTS = (ROOM, BED, DESK, DOOR, THP_ROOM, BATH, THP_DOOR)

BEDROOM = {
    "name": "Bedroom",
    "presence": [ROOM],
    "motion": [THP_ROOM, THP_DOOR],
    "exits": [DOOR, THP_DOOR],
    "bathroom": False,
    "exclusive": False,
    "spots": [
        {"name": "Bed", "sensor": BED, "role": "bed", "stillness": THP_ROOM, "sleep_entities": []},
        {"name": "Desk", "sensor": DESK},
    ],
}
BATHROOM = {
    "name": "Bathroom",
    "presence": [BATH],
    "bathroom": True,
    "exclusive": True,
    "humidity": HUM,
    "spots": [],
}
UNI_PLACE = {
    "name": "Uni",
    "ssids": ["eduroam"],
    "zones": ["zone.uni"],
    "mode": "any",
    "priority": 0,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/ in every test."""
    yield


def make_entry(
    hass,
    rooms=(BEDROOM, BATHROOM),
    places=(UNI_PLACE,),
    person: str | None = PERSON,
    **options,
) -> MockConfigEntry:
    """Add a home entry: the person (with the phone's SSID sensor), ``rooms`` and ``places``."""
    subentries = []
    if person:
        subentries.append(
            ConfigSubentryData(
                data={"person": person, "ssid_sensors": [SSID]},
                subentry_type="person",
                title="Will",
                unique_id=person,
            )
        )
    subentries += [
        ConfigSubentryData(data=r, subentry_type="room", title=r["name"], unique_id=None)
        for r in rooms
    ]
    subentries += [
        ConfigSubentryData(data=p, subentry_type="place", title=p["name"], unique_id=None)
        for p in places
    ]
    e = MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        version=2,
        data={"home_zone": "zone.home", "home_ssids": ["HallsNet"]},
        options=options,
        subentries_data=subentries,
    )
    e.add_to_hass(hass)
    return e


@pytest.fixture
def inputs(hass):
    """Every input at a quiet start: all sensors off, the person home, the phone on no network."""
    for e in INPUTS:
        hass.states.async_set(e, "off")
    hass.states.async_set(HUM, "37")
    hass.states.async_set(PERSON, "home", {"device_trackers": [TRACKER], "friendly_name": "Will"})
    hass.states.async_set(SSID, "Not Connected")
    hass.states.async_set("zone.home", "0", {"friendly_name": "Home"})
    hass.states.async_set("zone.uni", "0", {"friendly_name": "Uni of Lancashire"})


@pytest.fixture
def entry(hass, inputs):
    """The home entry (not yet set up)."""
    return make_entry(hass)


async def setup(hass, entry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
