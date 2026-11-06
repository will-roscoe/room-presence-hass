"""Config flow, options flow and the place subentry flow."""

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
import voluptuous as vol

from custom_components.room_presence.const import (
    CONF_BATHROOM,
    CONF_CROSSING_WINDOW,
    CONF_DOOR_WITNESSES,
    CONF_FP2_BED,
    CONF_FP2_ROOM,
    CONF_NAME,
    CONF_PHONE_TRACKER,
    DEFAULTS,
    DOMAIN,
)

from .conftest import BED, DOOR, ROOM, THP_DOOR, TRACKER, setup


async def start_user(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


USER_INPUT = {
    CONF_NAME: "Will",
    CONF_FP2_ROOM: ROOM,
    CONF_FP2_BED: BED,
    CONF_DOOR_WITNESSES: [DOOR, THP_DOOR],
    CONF_PHONE_TRACKER: TRACKER,
}


async def test_user_step_creates_entry(hass, inputs):
    result = await start_user(hass)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Will"
    assert result["data"][CONF_DOOR_WITNESSES] == [DOOR, THP_DOOR]
    assert result["data"][CONF_FP2_BED] == BED


async def test_door_witness_required(hass, inputs):
    result = await start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_DOOR_WITNESSES: []}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_DOOR_WITNESSES: "door_witness_required"}


async def test_same_fp2_room_twice_aborts(hass, entry):
    hass.config_entries.async_update_entry(entry, unique_id=ROOM)
    result = await start_user(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT


async def test_options_defaults_match_spec(hass, entry):
    await setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    defaults = {
        str(k): k.default() for k in result["data_schema"].schema if k.default is not vol.UNDEFINED
    }
    assert defaults == DEFAULTS


async def test_options_change_reloads(hass, entry):
    await setup(hass, entry)
    before = entry.runtime_data
    result = await hass.config_entries.options.async_init(entry.entry_id)
    bath = "binary_sensor.tuya_thp_2_presence"
    user = {**DEFAULTS, CONF_CROSSING_WINDOW: 45, CONF_BATHROOM: bath}
    result = await hass.config_entries.options.async_configure(result["flow_id"], user)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.runtime_data is not before
    assert entry.runtime_data.cfg.crossing_window.total_seconds() == 45
    assert entry.runtime_data.cfg.bathroom == bath


PLACE = {
    "name": "Building",
    "ssids": ["HallsNet"],
    "zones": [],
    "mode": "any",
    "priority": 1,
    "on_site": True,
}


async def add_place(hass, entry, data):
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "place"), context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], data)
    await hass.async_block_till_done()
    return result


async def test_place_subentry_create_edit_delete(hass, entry):
    await setup(hass, entry)
    result = await add_place(hass, entry, PLACE)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    slugs = {p.slug: p for p in entry.runtime_data.places}
    assert set(slugs) == {"uni", "building"}
    assert slugs["building"].on_site is True

    sub_id = next(s for s, sub in entry.subentries.items() if sub.title == "Building")
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "place"),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": sub_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**PLACE, "ssids": ["HallsNet", "HallsNet-5G"]}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    building = next(p for p in entry.runtime_data.places if p.slug == "building")
    assert building.ssids == ("HallsNet", "HallsNet-5G")

    hass.config_entries.async_remove_subentry(entry, sub_id)
    await hass.async_block_till_done()
    assert {p.slug for p in entry.runtime_data.places} == {"uni"}


async def test_place_name_reserved(hass, entry):
    await setup(hass, entry)
    result = await add_place(hass, entry, {**PLACE, "name": "Away"})
    assert result["errors"] == {"name": "place_name_reserved"}


async def test_place_name_taken(hass, entry):
    await setup(hass, entry)
    result = await add_place(hass, entry, {**PLACE, "name": "UNI"})
    assert result["errors"] == {"name": "place_name_taken"}


async def test_place_needs_ssid_or_zone(hass, entry):
    await setup(hass, entry)
    result = await add_place(hass, entry, {**PLACE, "ssids": [], "zones": []})
    assert result["errors"] == {"base": "place_needs_ssid_or_zone"}
