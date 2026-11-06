"""The room_presence.set_state action."""

from datetime import timedelta

from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.room_presence.const import DOMAIN

from .conftest import make_entry, setup


async def call(hass, **data) -> None:
    await hass.services.async_call(DOMAIN, "set_state", data, blocking=True)
    await hass.async_block_till_done()


async def test_set_state_immediate_and_stored(hass, entry, hass_storage):
    await setup(hass, entry)
    await call(hass, state="bed")
    coord = entry.runtime_data
    assert coord.snapshot.location == "bed"
    assert coord.snapshot.reason == "manual"
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}.manual"]["data"]
    assert [m["state"] for m in stored["calls"]] == ["bed"]


async def test_set_state_place(hass, entry):
    await setup(hass, entry)
    await call(hass, state="uni")
    assert entry.runtime_data.snapshot.location == "uni"


async def test_set_state_delay_and_cancel(hass, entry, freezer):
    await setup(hass, entry)
    await call(hass, state="bed", delay=60)
    await call(hass, state="desk", delay=30)
    coord = entry.runtime_data
    assert coord.snapshot.location == "away"
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert coord.snapshot.location == "desk"
    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert coord.snapshot.location == "desk"


async def test_set_state_requires_entry_id_with_two_entries(hass, entry):
    other = make_entry(hass, title="Guest")
    await setup(hass, entry)  # loading the integration sets up both entries
    assert other.runtime_data is not None
    with pytest.raises(ServiceValidationError):
        await call(hass, state="bed")
    await call(hass, state="bed", config_entry_id=other.entry_id)
    assert other.runtime_data.snapshot.location == "bed"
    assert entry.runtime_data.snapshot.location == "away"


@pytest.mark.parametrize("bad", ["unknown", "office"])
async def test_set_state_rejects_bad_state(hass, entry, bad):
    await setup(hass, entry)
    before = entry.runtime_data.snapshot
    with pytest.raises(ServiceValidationError):
        await call(hass, state=bad)
    assert entry.runtime_data.snapshot == before
