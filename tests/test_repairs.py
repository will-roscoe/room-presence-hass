"""Repair issues: missing entities and a missing person (with its fix flow)."""

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.room_presence.const import DOMAIN

from .conftest import BATHROOM, BEDROOM, PERSON, make_entry, setup

GONE = "binary_sensor.gone"


async def test_missing_entity_issue_raised_and_cleared(hass, inputs):
    entry = make_entry(hass, rooms=({**BEDROOM, "exits": [*BEDROOM["exits"], GONE]}, BATHROOM))
    await setup(hass, entry)
    issues = ir.async_get(hass)
    issue = issues.async_get_issue(DOMAIN, f"missing_entity_{GONE}")
    assert issue is not None and not issue.is_fixable
    assert issue.translation_placeholders == {"entity": GONE, "room": "Bedroom"}
    assert [i for (d, i) in issues.issues if d == DOMAIN] == [f"missing_entity_{GONE}"]
    hass.states.async_set(GONE, "off")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, f"missing_entity_{GONE}") is None


async def test_person_missing_fix_flow(hass, inputs):
    assert await async_setup_component(hass, "repairs", {})
    entry = make_entry(hass, person=None)
    await setup(hass, entry)
    assert hass.states.get("sensor.will_location") is None
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, "person_missing") is not None
    manager = repairs_flow_manager(hass)
    result = await manager.async_init(DOMAIN, context={"issue_id": "person_missing"})
    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "person")
    result = await manager.async_configure(result["flow_id"], {"person": PERSON})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    [person] = [s for s in entry.subentries.values() if s.subentry_type == "person"]
    assert (person.title, person.unique_id, person.data["person"]) == ("Will", PERSON, PERSON)
    assert issues.async_get_issue(DOMAIN, "person_missing") is None
    assert hass.states.get("sensor.will_location") is not None
