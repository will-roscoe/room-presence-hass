"""Places outside the room, matched from the phone's Wi-Fi network (SSID) and HA zone."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

#: SSID sensor states that say nothing about where the phone is.
MEANINGLESS_SSIDS = frozenset({"", "not connected", "<not connected>", "unavailable", "unknown"})


@dataclass(frozen=True)
class Place:
    """A configured place.

    ``ssids`` and ``zones`` (zone entity ids such as ``zone.uni``) are the evidence; ``mode``
    ``any`` needs one of them, ``all`` needs an SSID and a zone at once. Lower ``priority`` wins.
    ``on_site`` marks places inside the home building (they count as home, never as off-site).
    """

    slug: str
    name: str
    ssids: tuple[str, ...] = ()
    zones: tuple[str, ...] = ()
    mode: Literal["any", "all"] = "any"
    priority: int = 0
    on_site: bool = False


def _clean_ssid(ssid: str | None) -> str | None:
    if ssid is None or ssid.strip().lower() in MEANINGLESS_SSIDS:
        return None
    return ssid


def _clean_zone(zone: str | None) -> str | None:
    return zone if zone and zone.startswith("zone.") else None


def match_place(places: Sequence[Place], ssid: str | None, zone: str | None) -> Place | None:
    """Return the first place matching the phone's ``ssid`` and ``zone``, by priority then order.

    ``zone`` is a zone entity id; ``None``, ``not_home`` and any non-``zone.`` value mean no zone.
    Meaningless SSIDs (``Not Connected``, ``unavailable``, empty, ...) never match. Returns ``None``
    when nothing matches.
    """
    ssid, zone = _clean_ssid(ssid), _clean_zone(zone)
    for place in sorted(places, key=lambda p: p.priority):
        by_ssid = ssid is not None and ssid in place.ssids
        by_zone = zone is not None and zone in place.zones
        if (by_ssid and by_zone) if place.mode == "all" else (by_ssid or by_zone):
            return place
    return None
