"""Repair issues raised at setup, and the fix flow for a missing person.

:func:`async_update_issues` raises ``person_missing`` (fixable) while the entry has no person,
and ``missing_entity`` for each configured entity that is in neither the entity registry nor
the state machine, deleting the ones that no longer apply.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig
import voluptuous as vol

from .const import (
    CONF_EXITS,
    CONF_HOME_ZONE,
    CONF_HUMIDITY,
    CONF_MOTION,
    CONF_PERSON,
    CONF_PRESENCE,
    CONF_SENSOR,
    CONF_SLEEP_ENTITIES,
    CONF_SPOTS,
    CONF_SSID_SENSORS,
    CONF_STILLNESS,
    CONF_ZONES,
    DOMAIN,
    SUBENTRY_PERSON,
    SUBENTRY_PLACE,
    SUBENTRY_ROOM,
)
from .discovery import person_ssid_sensors

PERSON_MISSING = "person_missing"
MISSING_ENTITY = "missing_entity"


def configured_entities(entry: ConfigEntry) -> list[tuple[str, str]]:
    """Every entity the entry uses, as ``(entity_id, owner)``; the owner is the title of the
    person, room or place that uses it (the entry's title for the home zone)."""
    out = [(entry.data.get(CONF_HOME_ZONE) or "zone.home", entry.title)]
    for sub in entry.subentries.values():
        data, ids = sub.data, []
        if sub.subentry_type == SUBENTRY_PERSON:
            ids = [data.get(CONF_PERSON), *(data.get(CONF_SSID_SENSORS) or ())]
        elif sub.subentry_type == SUBENTRY_ROOM:
            ids = [
                *(data.get(CONF_PRESENCE) or ()),
                *(data.get(CONF_MOTION) or ()),
                *(data.get(CONF_EXITS) or ()),
                data.get(CONF_HUMIDITY),
            ]
            for spot in data.get(CONF_SPOTS) or ():
                ids += [spot.get(CONF_SENSOR), spot.get(CONF_STILLNESS)]
                ids += list(spot.get(CONF_SLEEP_ENTITIES) or ())
        elif sub.subentry_type == SUBENTRY_PLACE:
            ids = list(data.get(CONF_ZONES) or ())
        out += [(e, sub.title) for e in ids if e]
    return out


def async_update_issues(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Raise or delete the entry's ``person_missing`` and ``missing_entity`` issues."""
    if any(s.subentry_type == SUBENTRY_PERSON for s in entry.subentries.values()):
        ir.async_delete_issue(hass, DOMAIN, PERSON_MISSING)
    else:
        ir.async_create_issue(
            hass,
            DOMAIN,
            PERSON_MISSING,
            data={"entry_id": entry.entry_id},
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=PERSON_MISSING,
        )
    ents, wanted = er.async_get(hass), {}
    for entity_id, owner in configured_entities(entry):
        if ents.async_get(entity_id) is None and hass.states.get(entity_id) is None:
            wanted.setdefault(f"{MISSING_ENTITY}_{entity_id}", (entity_id, owner))
    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id.startswith(MISSING_ENTITY) and issue_id not in wanted:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
    for issue_id, (entity_id, owner) in wanted.items():
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=MISSING_ENTITY,
            translation_placeholders={"entity": entity_id, "room": owner},
        )


class PersonFixFlow(RepairsFlow):
    """Pick the person to track; adds the person subentry (which reloads the entry)."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Go to the form (HA passes the issue id here as input, which is not an answer)."""
        return await self.async_step_person()

    async def async_step_person(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return self.async_abort(reason="entry_removed")
        if user_input is not None:
            person = user_input[CONF_PERSON]
            st = self.hass.states.get(person)
            data = {CONF_PERSON: person, CONF_SSID_SENSORS: person_ssid_sensors(self.hass, person)}
            self.hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(
                    data=MappingProxyType(data),
                    subentry_type=SUBENTRY_PERSON,
                    title=st.name if st is not None else person,
                    unique_id=person,
                ),
            )
            return self.async_create_entry(data={})
        schema = vol.Schema(
            {vol.Required(CONF_PERSON): EntitySelector(EntitySelectorConfig(domain="person"))}
        )
        return self.async_show_form(step_id="person", data_schema=schema)


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Return the fix flow for ``person_missing`` (the only fixable issue)."""
    return PersonFixFlow((data or {})["entry_id"])
