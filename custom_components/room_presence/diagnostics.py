"""Diagnostics download: configuration, the current snapshot and the recent logs."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.core import HomeAssistant

from . import PresenceConfigEntry
from .const import CONF_HOME_SSIDS, CONF_SSIDS

REDACT = {CONF_HOME_SSIDS, CONF_SSIDS}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PresenceConfigEntry
) -> dict[str, Any]:
    """Return ``data``, ``options``, ``subentries`` (``type``, ``title``, ``data``), ``snapshot``,
    ``transitions`` (last 200) and ``edges`` (last 500 sensor changes); Wi-Fi names redacted,
    including the states of the phone's Wi-Fi sensors in ``edges``."""
    coord = entry.runtime_data
    ssid_sensors = set(coord.home.ssid_sensors)
    snap = asdict(coord.snapshot)
    snap["since"] = snap["since"].isoformat() if snap["since"] else None
    snap["degraded"] = sorted(snap["degraded"])
    return {
        "data": async_redact_data(dict(entry.data), REDACT),
        "options": dict(entry.options),
        "subentries": [
            {
                "type": s.subentry_type,
                "title": s.title,
                "data": async_redact_data(dict(s.data), REDACT),
            }
            for s in entry.subentries.values()
        ],
        "snapshot": snap,
        "transitions": [
            {"at": t.at.isoformat(), "old": t.old, "new": t.new, "reason": t.reason}
            for t in coord.transitions
        ],
        "edges": [
            {
                "at": e.at.isoformat(),
                "entity_id": e.entity_id,
                "state": REDACTED if e.entity_id in ssid_sensors else e.state,
            }
            for e in coord.edges
        ],
    }
