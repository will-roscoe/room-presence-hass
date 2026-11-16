"""Config flow (home and person), reconfigure, options, and the person, room and place subentry
flows."""

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_presence.const import DOMAIN

from .conftest import BATH, BED, DESK, DOOR, HUM, PERSON, ROOM, THP_ROOM, TRACKER, make_entry, setup


def suggested(result, key: str):
    for marker in result["data_schema"].schema:
        if marker == key:
            return (marker.description or {}).get("suggested_value")
    raise KeyError(key)


async def start_home(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


async def test_user_flow_creates_entry_and_person(hass, inputs):
    result = await start_home(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"person": PERSON, "home_zone": "zone.home", "home_ssids": ["HallsNet"]},
    )
    assert result["step_id"] == "ssids"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"ssid_sensors": ["sensor.pollux_ssid"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home"
    assert result["data"] == {"home_zone": "zone.home", "home_ssids": ["HallsNet"]}
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.version == 2
    (person,) = entry.subentries.values()
    assert person.subentry_type == "person"
    assert person.title == "Will"
    assert person.unique_id == PERSON
    assert dict(person.data) == {"person": PERSON, "ssid_sensors": ["sensor.pollux_ssid"]}


async def test_user_flow_suggests_ssid_sensors(hass, inputs):
    owner = MockConfigEntry(domain="mobile_app")
    owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("mobile_app", "vega")}
    )
    ents = er.async_get(hass)
    ents.async_get_or_create(
        "device_tracker", "mobile_app", "t", suggested_object_id="vega", device_id=device.id
    )
    ents.async_get_or_create(
        "sensor", "mobile_app", "s", suggested_object_id="vega_ssid", device_id=device.id
    )
    result = await start_home(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"person": PERSON, "home_zone": "zone.home"}
    )
    assert suggested(result, "ssid_sensors") == ["sensor.vega_ssid"]


async def test_single_instance(hass, entry):
    result = await start_home(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reconfigure_home(hass, entry):
    await setup(hass, entry)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"home_zone": "zone.uni", "home_ssids": []}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert dict(entry.data) == {"home_zone": "zone.uni", "home_ssids": []}


async def test_options_round_trip(hass, entry):
    await setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    values = {
        "crossing_window": 45,
        "move_window": 20,
        "recently_left_duration": 300,
        "sleep_quiet": 1800,
        "wake_on_time": 8,
        "bed_leave_grace": 20,
        "exit_pre_window": 180,
        "exit_quiet": 180,
        "exit_door_on_time": 8,
        "entry_window": 90,
        "exclusive_quiet": 90,
        "shower_window": 900,
        "shower_end": 180,
        "shower_rise": 7.5,
        "rebuild_lookback": 12,
        "entry_needs_crossing": True,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], values)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == values
    timings = entry.runtime_data.home.timings
    assert timings.crossing_window.total_seconds() == 45
    assert timings.shower_rise == 7.5


async def start_sub(hass, entry, kind: str):
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, kind), context={"source": SOURCE_USER}
    )


async def configure_sub(hass, result, data):
    return await hass.config_entries.subentries.async_configure(result["flow_id"], data)


async def test_person_subentry_add_aborts(hass, entry):
    await setup(hass, entry)
    result = await start_sub(hass, entry, "person")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_person"


async def test_person_subentry_reconfigure(hass, entry):
    await setup(hass, entry)
    person = next(s for s in entry.subentries.values() if s.subentry_type == "person")
    result = await entry.start_subentry_reconfigure_flow(hass, person.subentry_id)
    assert result["step_id"] == "reconfigure"
    result = await configure_sub(hass, result, {"person": PERSON, "ssid_sensors": []})
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries[person.subentry_id].data["ssid_sensors"] == []


ROOM_FORM = {
    "name": "Lounge",
    "presence": ["binary_sensor.lounge"],
    "motion": [],
    "exits": [],
    "bathroom": False,
    "exclusive": False,
}


async def test_room_flow_with_spots_and_bed(hass, entry):
    await setup(hass, entry)
    result = await start_sub(hass, entry, "room")
    assert result["step_id"] == "user"
    result = await configure_sub(hass, result, ROOM_FORM)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "spots"
    result = await configure_sub(hass, result, {"next_step_id": "add_spot"})
    assert result["step_id"] == "spot"
    result = await configure_sub(
        hass, result, {"name": "Sofa", "sensor": "binary_sensor.sofa", "role": "none"}
    )
    assert result["step_id"] == "spots"
    result = await configure_sub(hass, result, {"next_step_id": "add_spot"})
    result = await configure_sub(
        hass, result, {"name": "Day bed", "sensor": "binary_sensor.daybed", "role": "bed"}
    )
    assert result["step_id"] == "spot_bed"
    result = await configure_sub(hass, result, {"stillness": THP_ROOM, "sleep_entities": []})
    assert result["step_id"] == "spots"
    result = await configure_sub(hass, result, {"next_step_id": "finish"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    room = next(s for s in entry.subentries.values() if s.title == "Lounge")
    assert room.subentry_type == "room"
    assert [(s["name"], s["role"]) for s in room.data["spots"]] == [
        ("Sofa", None),
        ("Day bed", "bed"),
    ]
    assert room.data["spots"][1]["stillness"] == THP_ROOM
    assert room.data["humidity"] is None


async def test_room_flow_bathroom_asks_humidity(hass, entry):
    await setup(hass, entry)
    result = await start_sub(hass, entry, "room")
    result = await configure_sub(
        hass, result, {**ROOM_FORM, "name": "Shower room", "bathroom": True, "exclusive": True}
    )
    assert result["step_id"] == "humidity"
    result = await configure_sub(hass, result, {"humidity": HUM})
    assert result["step_id"] == "spots"
    result = await configure_sub(hass, result, {"next_step_id": "finish"})
    room = next(s for s in entry.subentries.values() if s.title == "Shower room")
    assert (room.data["bathroom"], room.data["exclusive"], room.data["humidity"]) == (
        True,
        True,
        HUM,
    )


@pytest.mark.parametrize(
    ("form", "field", "error"),
    [
        ({"name": "Home"}, "name", "name_reserved"),
        ({"name": "Just left"}, "name", "name_reserved"),
        ({"name": "Bedroom"}, "name", "name_taken"),
        ({"name": "Bed"}, "name", "name_taken"),
        ({"name": "Uni"}, "name", "name_taken"),
        ({"presence": []}, "presence", "presence_required"),
    ],
)
async def test_room_flow_errors(hass, entry, form, field, error):
    await setup(hass, entry)
    result = await start_sub(hass, entry, "room")
    result = await configure_sub(hass, result, {**ROOM_FORM, **form})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: error}


async def test_spot_name_errors(hass, entry):
    await setup(hass, entry)
    result = await start_sub(hass, entry, "room")
    result = await configure_sub(hass, result, ROOM_FORM)
    result = await configure_sub(hass, result, {"next_step_id": "add_spot"})
    result = await configure_sub(
        hass, result, {"name": "Sofa", "sensor": "binary_sensor.sofa", "role": "none"}
    )
    result = await configure_sub(hass, result, {"next_step_id": "add_spot"})
    for name, error in (
        ("Sofa", "spot_name_taken"),
        ("Desk", "name_taken"),
        ("Away", "name_reserved"),
    ):
        result = await configure_sub(
            hass, result, {"name": name, "sensor": "binary_sensor.x", "role": "none"}
        )
        assert result["errors"] == {"name": error}


async def test_room_reconfigure_reorders_and_removes_spots(hass, entry):
    await setup(hass, entry)
    bedroom = next(s for s in entry.subentries.values() if s.title == "Bedroom")
    result = await entry.start_subentry_reconfigure_flow(hass, bedroom.subentry_id)
    assert result["step_id"] == "reconfigure"
    assert suggested(result, "presence") == [ROOM]
    data = dict(bedroom.data)
    form = {k: data[k] for k in ("name", "presence", "motion", "exits", "bathroom", "exclusive")}
    result = await configure_sub(hass, result, form)
    assert result["step_id"] == "spots"
    result = await configure_sub(hass, result, {"next_step_id": "move_spot_up"})
    result = await configure_sub(hass, result, {"spot": "Desk"})
    assert result["step_id"] == "spots"
    result = await configure_sub(hass, result, {"next_step_id": "edit_spot"})
    result = await configure_sub(hass, result, {"spot": "Desk"})
    assert result["step_id"] == "spot"
    result = await configure_sub(hass, result, {"name": "Desk", "sensor": DOOR, "role": "none"})
    result = await configure_sub(hass, result, {"next_step_id": "finish"})
    assert result["reason"] == "reconfigure_successful"
    spots = entry.subentries[bedroom.subentry_id].data["spots"]
    assert [(s["name"], s["sensor"]) for s in spots] == [("Desk", DOOR), ("Bed", BED)]
    result = await entry.start_subentry_reconfigure_flow(hass, bedroom.subentry_id)
    result = await configure_sub(hass, result, form)
    result = await configure_sub(hass, result, {"next_step_id": "remove_spot"})
    result = await configure_sub(hass, result, {"spot": "Desk"})
    result = await configure_sub(hass, result, {"next_step_id": "finish"})
    spots = entry.subentries[bedroom.subentry_id].data["spots"]
    assert [s["name"] for s in spots] == ["Bed"]


PLACE_FORM = {"name": "Gym", "ssids": ["GymWiFi"], "zones": [], "mode": "any", "priority": 0}


async def test_place_flow(hass, entry):
    await setup(hass, entry)
    result = await start_sub(hass, entry, "place")
    result = await configure_sub(hass, result, PLACE_FORM)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    place = next(s for s in entry.subentries.values() if s.title == "Gym")
    assert "on_site" not in place.data


@pytest.mark.parametrize(
    ("form", "field", "error"),
    [
        ({"name": "Bedroom"}, "name", "name_taken"),
        ({"name": "Desk"}, "name", "name_taken"),
        ({"name": "Away"}, "name", "name_reserved"),
        ({"ssids": [], "zones": []}, "base", "place_needs_ssid_or_zone"),
    ],
)
async def test_place_flow_errors(hass, entry, form, field, error):
    await setup(hass, entry)
    result = await start_sub(hass, entry, "place")
    result = await configure_sub(hass, result, {**PLACE_FORM, **form})
    assert result["errors"] == {field: error}


async def test_subentry_change_reloads(hass, entry):
    await setup(hass, entry)
    before = entry.runtime_data
    result = await start_sub(hass, entry, "place")
    await configure_sub(hass, result, PLACE_FORM)
    await hass.async_block_till_done()
    assert entry.runtime_data is not before
    assert [p.slug for p in entry.runtime_data.places] == ["uni", "gym"]


async def test_entry_without_rooms_sets_up(hass, inputs):
    e = make_entry(hass, rooms=(), places=())
    await setup(hass, e)
    assert e.runtime_data.snapshot.key == "home"


def test_shared_constants():
    assert BATH and DESK and TRACKER  # the fixtures used across the flow tests exist
