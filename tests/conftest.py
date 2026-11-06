"""Shared fixtures: the live input entities (spec §4) and a config entry using them."""

from homeassistant.config_entries import ConfigSubentryData
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_presence.const import (
    CONF_DOOR_WITNESSES,
    CONF_FP2_BED,
    CONF_FP2_DESK,
    CONF_FP2_ROOM,
    CONF_MOVEMENT,
    CONF_NAME,
    CONF_PHONE_SSID,
    CONF_PHONE_TRACKER,
    DOMAIN,
)

ROOM = "binary_sensor.aqara_fp2_room_presence"
BED = "binary_sensor.aqara_fp2_bed_presence"
DESK = "binary_sensor.aqara_fp2_desk_presence"
DOOR = "binary_sensor.aqara_fp2_door_presence"
THP_DOOR = "binary_sensor.tuya_thp_3_presence"
THP_ROOM = "binary_sensor.tuya_thp_1_presence"
TRACKER = "device_tracker.vega"
SSID = "sensor.pollux_ssid"
INPUTS = (ROOM, BED, DESK, DOOR, THP_DOOR, THP_ROOM)

UNI_PLACE = {
    "name": "Uni",
    "ssids": ["eduroam"],
    "zones": ["zone.uni"],
    "mode": "any",
    "priority": 0,
    "on_site": False,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/ in every test."""
    yield


def make_entry(hass, title: str = "Will", **options) -> MockConfigEntry:
    """Add a room_presence entry on the live entity ids, with one place ``uni``."""
    e = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={
            CONF_NAME: title,
            CONF_FP2_ROOM: ROOM,
            CONF_FP2_BED: BED,
            CONF_FP2_DESK: DESK,
            CONF_DOOR_WITNESSES: [DOOR, THP_DOOR],
            CONF_PHONE_TRACKER: TRACKER,
            CONF_PHONE_SSID: SSID,
        },
        options={CONF_MOVEMENT: THP_ROOM, **options},
        subentries_data=[
            ConfigSubentryData(data=UNI_PLACE, subentry_type="place", title="Uni", unique_id="uni")
        ],
    )
    e.add_to_hass(hass)
    return e


@pytest.fixture
def inputs(hass):
    """Every input entity at a quiet starting state: all zones off, phone on no network."""
    for e in INPUTS:
        hass.states.async_set(e, "off")
    hass.states.async_set(TRACKER, "home")
    hass.states.async_set(SSID, "Not Connected")
    hass.states.async_set("zone.uni", "0", {"friendly_name": "Uni of Lancashire"})


@pytest.fixture
def entry(hass, inputs):
    """A room_presence entry named "Will" (not yet set up)."""
    return make_entry(hass)


async def setup(hass, entry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
