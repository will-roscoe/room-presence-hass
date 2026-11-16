"""Entities: the person's device (location, activity, state, binaries), room devices,
diagnostics."""

from datetime import timedelta

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.room_presence.const import DOMAIN

from .conftest import BATH, BATHROOM, BED, BEDROOM, HUM, PERSON, ROOM, THP_DOOR, make_entry, setup

PERSON_ENTITIES = {
    "sensor.will_location": "location",
    "sensor.will_activity": "activity",
    "sensor.will_state": "state",
    "binary_sensor.will_home": "home",
    "binary_sensor.will_just_left": "just_left",
    "binary_sensor.will_asleep": "asleep",
    "binary_sensor.will_showering": "showering",
}


def sub_id(entry, kind: str, title: str | None = None) -> str:
    return next(
        s.subentry_id
        for s in entry.subentries.values()
        if s.subentry_type == kind and (title is None or s.title == title)
    )


def state(hass, entity_id: str) -> str:
    return hass.states.get(entity_id).state


async def call(hass, **data) -> None:
    await hass.services.async_call(DOMAIN, "set_state", data, blocking=True)
    await hass.async_block_till_done()


async def set_input(hass, freezer, entity_id: str, value: str, after: float = 1) -> None:
    freezer.tick(timedelta(seconds=after))
    hass.states.async_set(entity_id, value)
    await hass.async_block_till_done()


async def advance(hass, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def test_person_entities_and_device(hass, entry):
    await setup(hass, entry)
    person = sub_id(entry, "person")
    ents, devs = er.async_get(hass), dr.async_get(hass)
    device = devs.async_get_device_by_identifier((DOMAIN, person), entry.entry_id)
    assert device is not None
    assert device.name == "Will"
    for entity_id, key in PERSON_ENTITIES.items():
        reg = ents.async_get(entity_id)
        assert reg is not None, entity_id
        assert reg.unique_id == f"{person}_{key}"
        assert reg.device_id == device.id
        assert reg.config_subentry_id == person
    home = hass.states.get("binary_sensor.will_home")
    assert home.state == "on"
    assert home.attributes["device_class"] == BinarySensorDeviceClass.OCCUPANCY
    assert "device_class" not in hass.states.get("binary_sensor.will_asleep").attributes
    loc = hass.states.get("sensor.will_location")
    assert loc.state == "Home"
    for attr in ("since", "room", "spot", "held", "place", "conflict", "reason", "phone_age"):
        assert attr in loc.attributes
    act = hass.states.get("sensor.will_activity")
    assert act.state == "awake"
    assert act.attributes["options"] == ["asleep", "showering", "awake", "away", "unknown"]


async def test_room_entities(hass, entry, freezer):
    await setup(hass, entry)
    bedroom, bathroom = sub_id(entry, "room", "Bedroom"), sub_id(entry, "room", "Bathroom")
    ents, devs = er.async_get(hass), dr.async_get(hass)
    for sub, name in ((bedroom, "Bedroom"), (bathroom, "Bathroom")):
        device = devs.async_get_device_by_identifier((DOMAIN, sub), entry.entry_id)
        assert (device.name, device.area_id) == (name, None)
    expected = {
        "binary_sensor.bedroom_occupied": (bedroom, "occupied"),
        "binary_sensor.bedroom_bed": (bedroom, "spot_bed"),
        "binary_sensor.bedroom_desk": (bedroom, "spot_desk"),
        "binary_sensor.bedroom_degraded": (bedroom, "degraded"),
        "binary_sensor.bathroom_occupied": (bathroom, "occupied"),
        "binary_sensor.bathroom_degraded": (bathroom, "degraded"),
    }
    for entity_id, (sub, key) in expected.items():
        reg = ents.async_get(entity_id)
        assert reg is not None, entity_id
        assert (reg.unique_id, reg.config_subentry_id) == (f"{sub}_{key}", sub)
    assert ents.async_get("binary_sensor.bedroom_degraded").entity_category is (
        EntityCategory.DIAGNOSTIC
    )
    await set_input(hass, freezer, ROOM, "on")
    await set_input(hass, freezer, BED, "on")
    on = {e for e in expected if state(hass, e) == "on"}
    assert on == {"binary_sensor.bedroom_occupied", "binary_sensor.bedroom_bed"}
    await set_input(hass, freezer, ROOM, "unavailable")
    degraded = hass.states.get("binary_sensor.bedroom_degraded")
    assert degraded.state == "on"
    assert degraded.attributes["entities"] == [ROOM]
    assert state(hass, "binary_sensor.bathroom_degraded") == "off"


async def test_room_device_in_its_area(hass, inputs):
    area = ar.async_get(hass).async_create("Main bedroom")
    entry = make_entry(hass, rooms=({**BEDROOM, "area_id": area.id}, BATHROOM))
    await setup(hass, entry)
    bedroom = sub_id(entry, "room", "Bedroom")
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, bedroom), entry.entry_id)
    assert device.area_id == area.id
    ents = er.async_get(hass)
    entity_id = ents.async_get_entity_id("binary_sensor", DOMAIN, f"{bedroom}_occupied")
    assert ents.async_get(entity_id).device_id == device.id


async def test_location_fixed_word_translated(hass, entry, monkeypatch):
    async def words(hass, language, category, integrations=None, config_flow=None):
        assert (category, list(integrations)) == ("entity", [DOMAIN])
        return {f"component.{DOMAIN}.entity.sensor.state.state.home": "Zuhause"}

    monkeypatch.setattr("custom_components.room_presence.entity.async_get_translations", words)
    await setup(hass, entry)
    assert state(hass, "sensor.will_location") == "Zuhause"
    assert state(hass, "sensor.will_state") == "Zuhause"
    await call(hass, location="away")
    assert state(hass, "sensor.will_location") == "Away"  # missing word: English fallback


async def test_state_text(hass, entry, freezer):
    await setup(hass, entry)
    await call(hass, activity="asleep")
    assert state(hass, "sensor.will_state") == "Bed (asleep)"
    assert state(hass, "sensor.will_location") == "Bed"
    assert state(hass, "sensor.will_activity") == "asleep"
    assert state(hass, "binary_sensor.will_asleep") == "on"
    await call(hass, location="desk", activity="awake")
    assert state(hass, "sensor.will_state") == "Desk"
    await call(hass, location="away")
    assert state(hass, "sensor.will_state") == "Away"
    assert state(hass, "binary_sensor.will_home") == "off"


async def test_state_text_showering(hass, entry, freezer):
    await setup(hass, entry)
    await set_input(hass, freezer, ROOM, "on")
    await set_input(hass, freezer, BATH, "on", after=360)
    await set_input(hass, freezer, HUM, "45", after=120)
    assert state(hass, "sensor.will_state") == "Bathroom (showering)"
    assert state(hass, "sensor.will_activity") == "showering"
    assert state(hass, "binary_sensor.will_showering") == "on"
    assert state(hass, "binary_sensor.bathroom_occupied") == "on"


async def test_state_text_just_left(hass, entry, freezer):
    await setup(hass, entry)
    await set_input(hass, freezer, ROOM, "on")
    await set_input(hass, freezer, THP_DOOR, "on", after=30)
    await set_input(hass, freezer, ROOM, "off", after=20)
    await advance(hass, freezer, 30)
    assert state(hass, "sensor.will_state") == "Just left"
    assert state(hass, "sensor.will_location") == "Home"
    assert state(hass, "binary_sensor.will_just_left") == "on"
    assert state(hass, "binary_sensor.will_home") == "on"


async def test_state_text_zone_name(hass, inputs, freezer):
    entry = make_entry(hass, places=())
    await setup(hass, entry)
    freezer.tick(timedelta(seconds=1))
    hass.states.async_set(PERSON, "Uni of Lancashire", {"friendly_name": "Will"})
    await hass.async_block_till_done()
    assert state(hass, "sensor.will_location") == "Uni of Lancashire"
    assert state(hass, "sensor.will_state") == "Uni of Lancashire"
    assert state(hass, "sensor.will_activity") == "away"


async def test_unrecorded_attributes(hass, entry):
    await setup(hass, entry)
    info = hass.states.get("sensor.will_location").state_info
    assert {"since", "phone_age", "reason"} <= info["unrecorded_attributes"]
    assert "room" not in info["unrecorded_attributes"]


async def test_entities_follow_subentry_removal(hass, entry):
    await setup(hass, entry)
    bathroom = sub_id(entry, "room", "Bathroom")
    assert hass.config_entries.async_remove_subentry(entry, bathroom)
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get("binary_sensor.bathroom_occupied") is None
    assert (
        dr.async_get(hass).async_get_device_by_identifier((DOMAIN, bathroom), entry.entry_id)
        is None
    )
    assert state(hass, "binary_sensor.bedroom_occupied") == "off"


async def test_diagnostics_redacts_ssids(hass, entry, hass_client, freezer):
    await setup(hass, entry)
    await set_input(hass, freezer, ROOM, "on")
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diag["data"]["home_ssids"] == "**REDACTED**"
    place = next(s for s in diag["subentries"] if s["type"] == "place")
    assert place["data"]["ssids"] == "**REDACTED**"
    assert place["data"]["zones"] == ["zone.uni"]
    assert diag["snapshot"]["key"] == "bedroom"
    assert diag["snapshot"]["activity"] == "awake"
    assert diag["transitions"][-1]["new"] == "bedroom"
    assert diag["edges"][-1]["entity_id"] == ROOM
    assert "options" in diag


@pytest.mark.parametrize("entity_id", ["sensor.will_state", "binary_sensor.bedroom_occupied"])
async def test_entities_unavailable_after_unload(hass, entry, entity_id):
    await setup(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert state(hass, entity_id) == "unavailable"
