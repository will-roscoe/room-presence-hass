"""The room_presence.set_state action."""

from datetime import timedelta

from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.room_presence.const import DOMAIN

from .conftest import setup


async def call(hass, **data) -> None:
    await hass.services.async_call(DOMAIN, "set_state", data, blocking=True)
    await hass.async_block_till_done()


async def test_set_state_immediate_and_stored(hass, entry, hass_storage):
    await setup(hass, entry)
    await call(hass, location="bed")
    coord = entry.runtime_data
    assert coord.snapshot.key == "bed"
    assert coord.snapshot.reason == "manual"
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}.manual_v2"]["data"]
    assert [(m["location"], m["activity"]) for m in stored["calls"]] == [("bed", None)]


async def test_set_state_place(hass, entry):
    await setup(hass, entry)
    await call(hass, location="uni")
    assert entry.runtime_data.snapshot.key == "uni"


async def test_set_state_location_and_activity(hass, entry):
    await setup(hass, entry)
    await call(hass, activity="asleep")
    assert entry.runtime_data.snapshot.key == "bed:asleep"
    await call(hass, location="desk", activity="awake")
    assert entry.runtime_data.snapshot.key == "desk"


async def test_set_state_delay_and_cancel(hass, entry, freezer):
    await setup(hass, entry)
    await call(hass, location="bed", delay=60)
    await call(hass, location="desk", delay=30)
    coord = entry.runtime_data
    assert coord.snapshot.key == "home"
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert coord.snapshot.key == "desk"
    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert coord.snapshot.key == "desk"


@pytest.mark.parametrize(
    ("data", "key"),
    [
        ({}, "nothing_to_set"),
        ({"location": "office"}, "invalid_location"),
        ({"location": "unknown"}, "invalid_location"),
        ({"activity": "showering"}, "invalid_activity"),
    ],
)
async def test_set_state_errors(hass, entry, data, key):
    await setup(hass, entry)
    before = entry.runtime_data.snapshot
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, **data)
    assert err.value.translation_key == key
    assert entry.runtime_data.snapshot == before


async def test_set_state_not_loaded(hass, entry):
    await setup(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, location="bed")
    assert err.value.translation_key == "not_loaded"
