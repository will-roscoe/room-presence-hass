"""Where the one tracked person is: in a room (and on which spot), just left, out, or unknown.

:class:`PersonTracker` reads every room's :class:`~.rooms.RoomSensors` and is told of each
:class:`~.rooms.SensorChange` after the room has applied it. Deadline work (the exit decision, the
spot settle, ``just_left`` expiry) runs in :meth:`PersonTracker.tick`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from .rooms import ZONE_SETTLE, RoomSensors, SensorChange
from .types import HomeConfig, Presence, Transition

_SEEN = frozenset({"presence", "spot"})


class PersonTracker:
    """The one person's place among the rooms (spec §5.1-§5.3).

    Read :attr:`presence`, :attr:`room`, :attr:`spot` and :attr:`held`; :attr:`reason` says why.
    Transitions use a view as ``old``/``new``: ``"<room>"``, ``"<room>/<spot>"``, ``"just_left"``,
    ``"out"`` or ``"unknown"``.
    """

    def __init__(self, home: HomeConfig, rooms: Mapping[str, RoomSensors]) -> None:
        """:param home: rooms and timings.
        :param rooms: every room's sensors, by slug; the caller feeds them before calling
            :meth:`on_change`.
        """
        self.home = home
        self.timings = home.timings
        self.rooms = rooms
        self._presence = Presence.UNKNOWN
        self._room: str | None = None
        self._spot: str | None = None
        self._since: datetime | None = None
        self._spot_since: datetime | None = None
        self._reason = "start"
        self._now: datetime | None = None
        self._last_detection: datetime | None = None
        self._exit_at: datetime | None = None
        #: When the sensors first showed the room without a spot while the person was on one.
        self._pending_drop: datetime | None = None
        #: (exit edge, decision time) of an exit waiting for another room to claim the person.
        self._pending_exit: tuple[datetime, datetime] | None = None

    # -- read-only view -------------------------------------------------------------------------

    @property
    def presence(self) -> Presence:
        """In a room, just left, out, or unknown."""
        return self._presence

    @property
    def room(self) -> str | None:
        """Slug of the room the person is in (detected or held), else ``None``."""
        return self._room

    @property
    def spot(self) -> str | None:
        """Slug of the spot the person is on, else ``None``."""
        return self._spot

    @property
    def since(self) -> datetime | None:
        """When the view last changed (back-dated where the rule says so)."""
        return self._since

    @property
    def spot_since(self) -> datetime | None:
        """When the room or spot last changed."""
        return self._spot_since

    @property
    def reason(self) -> str:
        """Why the person is where they are."""
        return self._reason

    @property
    def now(self) -> datetime | None:
        """Time of the latest change or tick seen."""
        return self._now

    @property
    def last_detection(self) -> datetime | None:
        """Last time the person's room saw someone, or a room was forced."""
        return self._last_detection

    @property
    def exit_at(self) -> datetime | None:
        """Start of the latest ``just_left`` (the exit edge), kept after it expires."""
        return self._exit_at

    @property
    def detected(self) -> bool:
        """True while in a room whose sensors see someone now."""
        return self._room is not None and self.rooms[self._room].occupied

    @property
    def held(self) -> bool:
        """True while in a room whose sensors see nobody now."""
        return self._room is not None and not self.rooms[self._room].occupied

    # -- input ----------------------------------------------------------------------------------

    def on_change(self, room_slug: str, ch: SensorChange) -> list[Transition]:
        """Apply a change of ``room_slug``'s sensors (already fed to it); return transitions."""
        out = self.tick(ch.at)
        at = self._advance(ch.at)
        if ch.kinds & _SEEN:
            out += self._on_seen(room_slug, ch, at)
        if self._room == room_slug and (ch.was_occupied or ch.occupied):
            self._last_detection = at
        return out

    def force(
        self,
        at: datetime,
        presence: Presence,
        room: str | None,
        spot: str | None,
        reason: str,
    ) -> list[Transition]:
        """Put the person at ``presence``/``room``/``spot`` at ``at`` (``set_state`` or the phone).

        A forced room counts as a detection at ``at``. Pending decisions are dropped.
        """
        at = self._advance(at)
        self._pending_drop = self._pending_exit = None
        if presence is Presence.IN_ROOM:
            self._last_detection = at
        return self._set(at, presence, room, spot, reason)

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
            self._advance(d)
            out += self._run_deadline(kind, d)

    # -- internals ------------------------------------------------------------------------------

    def _advance(self, at: datetime) -> datetime:
        if self._now is None or at > self._now:
            self._now = at
        return self._now

    def _view(self) -> str:
        if self._presence is Presence.IN_ROOM and self._room is not None:
            return self._room if self._spot is None else f"{self._room}/{self._spot}"
        return self._presence.value

    def _set(
        self,
        at: datetime,
        presence: Presence,
        room: str | None,
        spot: str | None,
        reason: str,
    ) -> list[Transition]:
        old = self._view()
        if presence is not Presence.IN_ROOM:
            room = spot = None
        if (room, spot) != (self._room, self._spot):
            self._spot_since = at
        if presence is Presence.JUST_LEFT and self._presence is not Presence.JUST_LEFT:
            self._exit_at = at
        self._presence, self._room, self._spot, self._reason = presence, room, spot, reason
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
            out.append(("exit", self._pending_exit[1]))
        if self._presence is Presence.JUST_LEFT and self._since is not None:
            out.append(("expire", self._since + self.timings.recently_left_duration))
        return out

    def _run_deadline(self, kind: str, d: datetime) -> list[Transition]:
        if kind == "drop":
            at, self._pending_drop = self._pending_drop, None
            room = self._room
            if self._presence is Presence.IN_ROOM and room and self.rooms[room].occupied and at:
                return self._set(at, Presence.IN_ROOM, room, self.rooms[room].spot_now(), "spot")
            return []
        if kind == "exit":
            pending, self._pending_exit = self._pending_exit, None
            assert pending is not None
            if self._presence is Presence.IN_ROOM and self.held:
                return self._set(pending[0], Presence.JUST_LEFT, None, None, "crossing")
            return []
        return self._set(d, Presence.OUT, None, None, "just_left_expired")

    def _exit_edge(self, room_slug: str, at: datetime) -> datetime | None:
        lo = at - self.timings.crossing_window
        return self.rooms[room_slug].latest_edge(("exit",), lo, at)

    def _entry_allowed(self, room_slug: str, at: datetime) -> bool:
        """Entry from out or just left: only through a room with exits (spec §5.2)."""
        rooms_with_exits = [r.slug for r in self.home.rooms if r.exits]
        if rooms_with_exits and room_slug not in rooms_with_exits:
            return False
        if self.timings.entry_needs_crossing:
            return self._exit_edge(room_slug, at) is not None
        return True

    def _on_seen(self, room_slug: str, ch: SensorChange, at: datetime) -> list[Transition]:
        sensors = self.rooms[room_slug]
        if self._presence is Presence.UNKNOWN:
            return self._from_unknown(room_slug, at)
        if self._presence is not Presence.IN_ROOM:
            if sensors.occupied and self._entry_allowed(room_slug, at):
                self._pending_drop = None
                return self._set(at, Presence.IN_ROOM, room_slug, sensors.spot_now(), "entry")
            return []
        if room_slug == self._room:
            return self._in_own_room(room_slug, ch, at)
        return []

    def _from_unknown(self, room_slug: str, at: datetime) -> list[Transition]:
        if self.rooms[room_slug].occupied:
            target = room_slug
        else:
            target = next((s for s, r in self.rooms.items() if r.occupied), None)
        if target is not None:
            spot = self.rooms[target].spot_now()
            return self._set(at, Presence.IN_ROOM, target, spot, "detected")
        if all(not r.degraded for r in self.rooms.values()):
            return self._set(at, Presence.OUT, None, None, "detected")
        return []

    def _in_own_room(self, room_slug: str, ch: SensorChange, at: datetime) -> list[Transition]:
        sensors = self.rooms[room_slug]
        if sensors.occupied:
            self._pending_exit = None
            spot = sensors.spot_now()
            if spot is None and self._spot is not None:
                if self._pending_drop is None:
                    self._pending_drop = at
                return []
            self._pending_drop = None
            return self._set(at, Presence.IN_ROOM, room_slug, spot, "spot")
        if not ch.was_occupied:
            return []
        # The room went empty: an exit crossing waits for the move window, otherwise held.
        self._pending_drop = None
        edge = self._exit_edge(room_slug, at)
        if edge is None:
            self._reason = "held"
            return []
        self._pending_exit = (edge, at + self.timings.move_window)
        return []
