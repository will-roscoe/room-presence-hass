"""Room layer: in the room (and where), recently left, or out, from FP2 zones and door witnesses.

:class:`RoomLayer` is fed every input :class:`~.types.StateEvent` in time order and returns the
room-view transitions each one causes. Deadline work (the bathroom decision, the zone settle,
``recently_left`` expiry) happens in :meth:`RoomLayer.tick`, which :meth:`RoomLayer.feed` also runs
up to each event's time.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from .types import EngineConfig, RoomState, StateEvent, Sub, Transition

#: How long a drop to the plain ``room`` sub-location waits for the other FP2 zones. Sub-zone and
#: room-zone edges lag each other by up to 7 s (measured 4-5 Nov), so a bed-off then room-off one
#: second apart is someone lying still, not someone getting up.
ZONE_SETTLE = timedelta(seconds=10)
#: How long off->on edges are remembered; longer than every window that looks back at them.
EDGE_MEMORY = timedelta(minutes=15)
#: States after which an ``on`` is not an edge: the sensor coming back, not something moving.
UNSEEN = frozenset({"unavailable", "unknown"})


class RoomLayer:
    """The room layer of one tracked room (spec §5.2).

    Read :attr:`state`, :attr:`sub` and :attr:`held` for the answer and :attr:`reason` for why.
    Transitions use the room view as ``old``/``new``: the :class:`~.types.Sub` value while
    ``in_room``, otherwise the :class:`~.types.RoomState` value.
    """

    def __init__(self, cfg: EngineConfig) -> None:
        """:param cfg: entity roles and timings."""
        self.cfg = cfg
        self._states: dict[str, str] = {}
        self._edges: dict[str, list[datetime]] = {}
        self._now: datetime | None = None
        self._state = RoomState.UNKNOWN
        self._sub: Sub | None = None
        self._since: datetime | None = None
        self._sub_since: datetime | None = None
        self._reason = "start"
        self._last_detection: datetime | None = None
        #: Crossing edge of an exit waiting for the bathroom decision.
        self._pending_exit: datetime | None = None
        #: When the zones first said plain ``room`` while the sub-location was bed or desk.
        self._pending_drop: datetime | None = None

    # -- read-only view -------------------------------------------------------------------------

    @property
    def state(self) -> RoomState:
        """Room state."""
        return self._state

    @property
    def sub(self) -> Sub | None:
        """Sub-location while ``in_room`` (detected or held), else ``None``."""
        return self._sub

    @property
    def detected(self) -> bool:
        """True while an FP2 room/bed/desk zone or the bathroom sensor is on."""
        cfg = self.cfg
        return any(self._on(e) for e in (cfg.fp2_room, cfg.fp2_bed, cfg.fp2_desk, cfg.bathroom))

    @property
    def held(self) -> bool:
        """True while ``in_room`` with nothing detecting now."""
        return self._state is RoomState.IN_ROOM and not self.detected

    @property
    def last_detection(self) -> datetime | None:
        """Last time something was detected (or a room state was forced), else ``None``."""
        return self._last_detection

    @property
    def since(self) -> datetime | None:
        """When the room view last changed (back-dated where the rule says so)."""
        return self._since

    @property
    def sub_since(self) -> datetime | None:
        """When :attr:`sub` last changed."""
        return self._sub_since

    @property
    def reason(self) -> str:
        """Why the room layer is in its current state."""
        return self._reason

    @property
    def degraded(self) -> bool:
        """True while the FP2 room zone is unavailable."""
        return False

    @property
    def now(self) -> datetime | None:
        """Time of the latest event or tick seen."""
        return self._now

    def state_of(self, entity_id: str | None) -> str | None:
        """Last state seen for ``entity_id`` (``None`` if never seen)."""
        return None if entity_id is None else self._states.get(entity_id)

    # -- input ----------------------------------------------------------------------------------

    def feed(self, ev: StateEvent) -> list[Transition]:
        """Apply one input change and return the transitions it causes.

        Deadlines up to ``ev.at`` run first. A repeated state is ignored; an event older than the
        latest one seen is applied at the latest time.
        """
        out = self.tick(ev.at)
        at = ev.at if self._now is None or ev.at > self._now else self._now
        self._now = at
        prev = self._states.get(ev.entity_id)
        if prev == ev.state:
            return out
        was_detected = self.detected
        self._states[ev.entity_id] = ev.state
        edge = ev.state == "on" and prev not in UNSEEN
        if edge:
            edges = self._edges.setdefault(ev.entity_id, [])
            edges.append(at)
            while edges and edges[0] < at - EDGE_MEMORY:
                edges.pop(0)
        if was_detected or self.detected:
            self._last_detection = at
        out += self._evaluate(at, ev.entity_id, edge)
        return out

    def force(
        self, at: datetime, state: RoomState, sub: Sub | None, reason: str
    ) -> list[Transition]:
        """Set the room layer to ``state``/``sub`` at ``at`` (a ``set_state`` call).

        A forced ``in_room`` counts as a detection at ``at``. Pending decisions are dropped.
        """
        at = at if self._now is None or at > self._now else self._now
        self._now = at
        self._pending_exit = self._pending_drop = None
        if state is RoomState.IN_ROOM:
            self._last_detection = at
        return self._set(at, state, sub, reason)

    def next_deadline(self) -> datetime | None:
        """Earliest time :meth:`tick` has work to do, or ``None``."""
        times = [d for _, d in self._deadlines()]
        return min(times) if times else None

    def tick(self, now: datetime) -> list[Transition]:
        """Run every deadline due at or before ``now`` and return the transitions."""
        out: list[Transition] = []
        while True:
            due = sorted((d, k) for k, d in self._deadlines() if d <= now)
            if not due:
                return out
            d, kind = due[0]
            self._now = d if self._now is None or d > self._now else self._now
            out += self._run_deadline(kind, d)

    # -- internals ------------------------------------------------------------------------------

    def _on(self, entity_id: str | None) -> bool:
        return entity_id is not None and self._states.get(entity_id) == "on"

    def _zone_sub(self) -> Sub | None:
        cfg = self.cfg
        if self._on(cfg.fp2_bed):
            return Sub.BED
        if self._on(cfg.fp2_desk):
            return Sub.DESK
        if self._on(cfg.fp2_room):
            return Sub.ROOM
        return None

    def _view(self) -> str:
        if self._state is RoomState.IN_ROOM and self._sub is not None:
            return self._sub.value
        return self._state.value

    def _latest_edge(
        self, entities: Iterable[str | None], lo: datetime, hi: datetime
    ) -> datetime | None:
        hits = [
            t for e in entities if e is not None for t in self._edges.get(e, ()) if lo <= t <= hi
        ]
        return max(hits) if hits else None

    def _set(
        self, at: datetime, state: RoomState, sub: Sub | None, reason: str
    ) -> list[Transition]:
        old = self._view()
        sub = sub if state is RoomState.IN_ROOM else None
        if sub != self._sub:
            self._sub_since = at
        self._state, self._sub, self._reason = state, sub, reason
        new = self._view()
        if new == old:
            return []
        self._since = at
        return [Transition(at, old, new, reason)]

    def _deadlines(self) -> list[tuple[str, datetime]]:
        out = []
        if self._pending_drop is not None:
            out.append(("drop", self._pending_drop + ZONE_SETTLE))
        if self._pending_exit is not None:
            out.append(("exit", self._pending_exit + self.cfg.bathroom_window))
        if self._state is RoomState.RECENTLY_LEFT and self._since is not None:
            out.append(("expire", self._since + self.cfg.recently_left_duration))
        return out

    def _run_deadline(self, kind: str, d: datetime) -> list[Transition]:
        if kind == "drop":
            at, self._pending_drop = self._pending_drop, None
            zsub = self._zone_sub()
            if self._state is RoomState.IN_ROOM and zsub is not None and at is not None:
                return self._set(at, RoomState.IN_ROOM, zsub, "fp2_zone")
            return []
        if kind == "exit":
            edge, self._pending_exit = self._pending_exit, None
            assert edge is not None
            return self._set(edge, RoomState.RECENTLY_LEFT, None, "crossing")
        return self._set(d, RoomState.OUT, None, "recently_left_expired")

    def _evaluate(self, at: datetime, entity_id: str, edge: bool) -> list[Transition]:
        cfg = self.cfg
        if entity_id in (cfg.fp2_room, cfg.fp2_bed, cfg.fp2_desk):
            return self._on_zone_change(at)
        if (
            entity_id == cfg.bathroom
            and edge
            and self._pending_exit is not None
            and at <= self._pending_exit + cfg.bathroom_window
        ):
            self._pending_exit = None
            return self._set(at, RoomState.IN_ROOM, Sub.BATHROOM, "bathroom")
        return []

    def _on_zone_change(self, at: datetime) -> list[Transition]:
        cfg = self.cfg
        zsub = self._zone_sub()
        if zsub is not None:
            if self._state is RoomState.IN_ROOM:
                self._pending_exit = None
                if zsub is Sub.ROOM and self._sub in (Sub.BED, Sub.DESK):
                    if self._pending_drop is None:
                        self._pending_drop = at
                    return []
                self._pending_drop = None
                return self._set(at, RoomState.IN_ROOM, zsub, "fp2_zone")
            if (
                self._state is not RoomState.UNKNOWN
                and cfg.entry_needs_crossing
                and self._latest_edge(cfg.door_witnesses, at - cfg.crossing_window, at) is None
            ):
                return []
            reason = "fp2_zone" if self._state is RoomState.UNKNOWN else "entry"
            self._pending_drop = None
            return self._set(at, RoomState.IN_ROOM, zsub, reason)

        # The FP2 went empty: room, bed and desk all off.
        self._pending_drop = None
        if self._state is RoomState.UNKNOWN:
            return self._set(at, RoomState.OUT, None, "fp2_zone")
        if (
            self._state is not RoomState.IN_ROOM
            or self._sub is Sub.BATHROOM
            or self._pending_exit is not None
        ):
            return []
        crossing = self._latest_edge(cfg.door_witnesses, at - cfg.crossing_window, at)
        if crossing is None:
            self._reason = "held"
            return []
        if cfg.bathroom is None:
            return self._set(crossing, RoomState.RECENTLY_LEFT, None, "crossing")
        if self._latest_edge((cfg.bathroom,), crossing, crossing + cfg.bathroom_window):
            return self._set(at, RoomState.IN_ROOM, Sub.BATHROOM, "bathroom")
        if crossing + cfg.bathroom_window <= at:
            return self._set(crossing, RoomState.RECENTLY_LEFT, None, "crossing")
        self._pending_exit = crossing
        return []
