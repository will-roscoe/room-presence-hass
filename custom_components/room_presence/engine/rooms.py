"""Per-room sensor tracking: what each sensor of one room says, and its recent off→on edges.

:class:`RoomSensors` knows nothing about the person; it answers "is anyone seen here, on which
spot, and when did each kind of sensor last fire". :class:`~.person.PersonTracker` reads it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .types import RoomConfig, StateEvent, Timings

#: States after which an ``on`` is not an edge: the sensor coming back, not something moving.
UNSEEN = frozenset({"unavailable", "unknown"})
#: How long a drop from a spot to no spot waits for the other sensors. Spot and presence edges lag
#: each other by up to 7 s (measured 4-5 Nov), so a bed-off then room-off one second apart is
#: someone lying still, not someone getting up.
ZONE_SETTLE = timedelta(seconds=10)


@dataclass(frozen=True)
class SensorChange:
    """One sensor of a room changed.

    ``kinds`` are its roles in this room; ``edge`` means an off→on change (a first sighting counts,
    a return from ``unavailable``/``unknown`` does not); ``on_at`` is when the sensor last turned
    on, reported on the change that ends that on-interval. ``was_*``/``occupied``/``degraded`` are
    the room's state before and after.
    """

    at: datetime
    entity_id: str
    kinds: frozenset[str]
    prev: str | None
    state: str
    edge: bool
    on_at: datetime | None
    was_occupied: bool
    occupied: bool
    was_degraded: bool
    degraded: bool


class RoomSensors:
    """The sensors of one room: current states, occupancy, the spot seen now, and recent edges."""

    def __init__(self, room: RoomConfig, timings: Timings) -> None:
        """:param room: the room and its sensors.
        :param timings: decides how long edges are kept (:attr:`Timings.longest_lookback`).
        """
        self.room = room
        self._memory = timings.longest_lookback
        self._kinds: dict[str, set[str]] = {}
        for entity_id, kind in room.roles():
            self._kinds.setdefault(entity_id, set()).add(kind)
        self._states: dict[str, str] = {}
        self._edges: dict[str, list[datetime]] = {}
        self._on_at: dict[str, datetime] = {}

    @property
    def occupied(self) -> bool:
        """True while any presence or spot sensor is ``on``."""
        return any(self._on(e) for e in self._entities(("presence", "spot")))

    @property
    def degraded(self) -> bool:
        """True while every presence sensor is unavailable, unknown or has never reported."""
        return all(self._states.get(e) in (None, *UNSEEN) for e in self.room.presence)

    def spot_now(self) -> str | None:
        """Return the first spot, in list order, whose sensor is ``on``; ``None`` if none is."""
        for spot in self.room.spots:
            if self._on(spot.sensor):
                return spot.slug
        return None

    def state_of(self, entity_id: str) -> str | None:
        """Return the last state seen for ``entity_id``, ``None`` if never seen."""
        return self._states.get(entity_id)

    def available(self, kinds: Iterable[str]) -> list[str]:
        """Return this room's sensors of ``kinds`` whose state is ``on`` or ``off``."""
        return [e for e in self._entities(kinds) if self._states.get(e) in ("on", "off")]

    def latest_edge(self, kinds: Iterable[str], lo: datetime, hi: datetime) -> datetime | None:
        """Return the latest edge in ``[lo, hi]`` from this room's sensors of ``kinds``."""
        hits = self._edges_in(kinds, lo, hi)
        return max(hits) if hits else None

    def first_edge(self, kinds: Iterable[str], lo: datetime, hi: datetime) -> datetime | None:
        """Return the earliest edge in ``[lo, hi]`` from this room's sensors of ``kinds``."""
        hits = self._edges_in(kinds, lo, hi)
        return min(hits) if hits else None

    def edges_of(self, entity_id: str) -> list[datetime]:
        """Return the remembered edges of one sensor, oldest first."""
        return list(self._edges.get(entity_id, ()))

    def feed(self, ev: StateEvent) -> SensorChange | None:
        """Apply one state change; return what changed, or ``None`` for another room's entity or a
        repeated state."""
        kinds = self._kinds.get(ev.entity_id)
        if kinds is None:
            return None
        prev = self._states.get(ev.entity_id)
        if prev == ev.state:
            return None
        was_occupied, was_degraded = self.occupied, self.degraded
        self._states[ev.entity_id] = ev.state
        edge = ev.state == "on" and prev not in UNSEEN
        on_at = self._on_at.pop(ev.entity_id, None)
        if edge:
            self._on_at[ev.entity_id] = ev.at
            edges = self._edges.setdefault(ev.entity_id, [])
            edges.append(ev.at)
            while edges and edges[0] < ev.at - self._memory:
                edges.pop(0)
        return SensorChange(
            at=ev.at,
            entity_id=ev.entity_id,
            kinds=frozenset(kinds),
            prev=prev,
            state=ev.state,
            edge=edge,
            on_at=on_at,
            was_occupied=was_occupied,
            occupied=self.occupied,
            was_degraded=was_degraded,
            degraded=self.degraded,
        )

    # -- internals ------------------------------------------------------------------------------

    def _on(self, entity_id: str) -> bool:
        return self._states.get(entity_id) == "on"

    def _entities(self, kinds: Iterable[str]) -> list[str]:
        wanted = set(kinds)
        return [e for e, k in self._kinds.items() if k & wanted]

    def _edges_in(self, kinds: Iterable[str], lo: datetime, hi: datetime) -> list[datetime]:
        return [t for e in self._entities(kinds) for t in self._edges.get(e, ()) if lo <= t <= hi]
