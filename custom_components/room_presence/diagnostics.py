"""Diagnostics download: configuration, places, the current snapshot and the recent logs."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import PresenceConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PresenceConfigEntry
) -> dict[str, Any]:
    """Return ``data``, ``options``, ``places``, ``snapshot``, ``transitions`` and ``edges``."""
    coord = entry.runtime_data
    snap = asdict(coord.snapshot)
    snap["since"] = snap["since"].isoformat() if snap["since"] else None
    return {
        "data": dict(entry.data),
        "options": dict(entry.options),
        "places": [asdict(p) for p in coord.places],
        "snapshot": snap,
        "transitions": [
            {"at": t.at.isoformat(), "old": t.old, "new": t.new, "reason": t.reason}
            for t in coord.transitions
        ],
        "edges": [
            {"at": e.at.isoformat(), "entity_id": e.entity_id, "state": e.state}
            for e in coord.edges
        ],
    }
