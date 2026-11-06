"""Entities: the location enum sensor, the convenience binary sensors, diagnostics."""

from homeassistant.config_entries import ConfigSubentry, ConfigSubentryData
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.room_presence.const import DOMAIN
from custom_components.room_presence.engine.machine import LOCATIONS_FIXED

from .conftest import ROOM, make_entry, setup

BINARY = ("in_room", "in_bed", "at_desk", "in_bathroom", "sleeping", "recently_left", "home")
EXPECTED_ON = {
    "bed": {"in_room", "in_bed", "home"},
    "desk": {"in_room", "at_desk", "home"},
    "room": {"in_room", "home"},
    "bathroom": {"in_room", "in_bathroom", "home"},
    "sleeping": {"in_room", "in_bed", "sleeping", "home"},
    "recently_left": {"recently_left", "home"},
    "away": set(),
    "uni": set(),
    "building": {"home"},
}
BUILDING = ConfigSubentryData(
    data={
        "name": "Building",
        "ssids": ["HallsNet"],
        "zones": [],
        "mode": "any",
        "priority": 1,
        "on_site": True,
    },
    subentry_type="place",
    title="Building",
    unique_id="building",
)


@pytest.fixture
def entry(hass, inputs):
    """The "Will" entry with a second, on-site place "building"."""
    e = make_entry(hass)
    hass.config_entries.async_add_subentry(e, ConfigSubentry(**BUILDING))
    return e


async def set_location(hass, state: str) -> None:
    await hass.services.async_call(DOMAIN, "set_state", {"state": state}, blocking=True)
    await hass.async_block_till_done()


async def test_location_sensor_options_include_places(hass, entry):
    await setup(hass, entry)
    st = hass.states.get("sensor.will_location")
    assert st.state == "away"
    assert st.attributes["options"] == [*LOCATIONS_FIXED, "uni", "building"]
    for attr in ("since", "room_state", "held", "place", "conflict", "reason", "phone_age"):
        assert attr in st.attributes


@pytest.mark.parametrize("location", sorted(EXPECTED_ON))
async def test_binary_sensors_follow_location(hass, entry, location):
    await setup(hass, entry)
    await set_location(hass, location)
    assert hass.states.get("sensor.will_location").state == location
    on = {key for key in BINARY if hass.states.get(f"binary_sensor.will_{key}").state == "on"}
    assert on == EXPECTED_ON[location]


async def test_home_true_for_onsite_place_only(hass, entry):
    await setup(hass, entry)
    await set_location(hass, "building")
    assert hass.states.get("binary_sensor.will_home").state == "on"
    await set_location(hass, "uni")
    assert hass.states.get("binary_sensor.will_home").state == "off"


async def test_degraded_is_diagnostic(hass, entry):
    await setup(hass, entry)
    reg = er.async_get(hass)
    assert reg.async_get("binary_sensor.will_degraded").entity_category is EntityCategory.DIAGNOSTIC
    assert hass.states.get("binary_sensor.will_degraded").state == "off"
    hass.states.async_set(ROOM, "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.will_degraded").state == "on"


async def test_diagnostics_contains_transitions(hass, entry, hass_client):
    await setup(hass, entry)
    await set_location(hass, "bed")
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diag["transitions"][-1]["new"] == "bed"
    assert diag["transitions"][-1]["reason"] == "manual"
    assert {p["slug"] for p in diag["places"]} == {"uni", "building"}
    assert {str(p["ssids"]) for p in diag["places"]} == {"**REDACTED**"}
    assert "edges" in diag and "options" in diag
    assert diag["snapshot"]["location"] == "bed"
