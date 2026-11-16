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
        #: Fallback exit candidate: (room, exit edge, exit off), waiting for the quiet period.
        self._fallback_exit: tuple[str, datetime, datetime] | None = None
        #: The last (entity, time, state) the fallback rules saw, so a sensor shared by two rooms
        #: is counted once.
        self._fallback_seen: tuple[str, datetime, str] | None = None
        #: When the person entered the current room.
        self._room_since: datetime | None = None
        #: The room the person was in before the current one.
        self._came_from: str | None = None
        #: Room the person is pinned to while showering.
        self._pinned: str | None = None
        #: Start of the current quiet spell in an exclusive room whose neighbour stays occupied.
        self._quiet_start: datetime | None = None

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
    def came_from(self) -> str | None:
        """The room the person was in before the current one, if any."""
        return self._came_from

    @property
    def pinned(self) -> str | None:
        """Room the person is pinned to (showering), else ``None``."""
        return self._pinned

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
        out += self._fallbacks(ch, at)
        if self._room == room_slug and (ch.was_occupied or ch.occupied):
            self._last_detection = at
        self._update_quiet(at)
        return out

    def pin(self, at: datetime, room_slug: str) -> list[Transition]:
        """Put the person in ``room_slug`` (showering); other rooms are ignored until unpinned."""
        at = self._advance(at)
        out: list[Transition] = []
        if self._presence is not Presence.IN_ROOM or self._room != room_slug:
            out = self._move(at, room_slug, "shower")
        self._pinned = room_slug
        self._update_quiet(at)
        return out

    def unpin(self, at: datetime | None = None) -> None:
        """End a :meth:`pin` at ``at`` (default: the latest time seen)."""
        self._pinned = None
        now = self._advance(at) if at is not None else self._now
        if now is not None:
            self._update_quiet(now)

    def fallback_sets(self, room_slug: str) -> tuple[set[str], set[str]]:
        """Return the fallback ``(motion, others)`` sensors of a room.

        ``motion`` is the room's motion sensors plus every other room's presence sensors;
        ``others`` is ``motion`` without the room's exits.
        """
        room = self.home.room(room_slug)
        motion = set(room.motion) | {
            e for r in self.home.rooms if r.slug != room_slug for e in r.presence
        }
        return motion, motion - set(room.exits)

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
        self._pending_drop = self._pending_exit = self._fallback_exit = None
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
                self._advance(now)
                return out
            d, kind = due[0]
            self._advance(d)
            out += self._run_deadline(kind, d)
            self._update_quiet(d)

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
        if room != self._room:
            self._room_since = at
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
        if self._fallback_exit is not None:
            out.append(("fallback_exit", self._fallback_exit[2] + self.timings.exit_quiet))
        if self._quiet_start is not None:
            out.append(("quiet", self._quiet_start + self.timings.exclusive_quiet))
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
        if kind == "quiet":
            self._quiet_start = None
            back = self._came_from
            if self._quiet_holds() and back is not None:
                return self._move(d, back, "exclusive_quiet")
            return []
        if kind == "fallback_exit":
            candidate, self._fallback_exit = self._fallback_exit, None
            assert candidate is not None
            if self._presence is Presence.IN_ROOM and self._room == candidate[0]:
                return self._set(candidate[1], Presence.JUST_LEFT, None, None, "fallback_exit")
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
            if sensors.occupied and not sensors.degraded and self._entry_allowed(room_slug, at):
                self._pending_drop = None
                return self._set(at, Presence.IN_ROOM, room_slug, sensors.spot_now(), "entry")
            return []
        if self._pinned is not None and room_slug != self._pinned:
            return []
        if room_slug == self._room:
            return self._in_own_room(room_slug, ch, at)
        return self._other_room(room_slug, ch, at)

    def _other_room(self, room_slug: str, ch: SensorChange, at: datetime) -> list[Transition]:
        """Another room sees someone while the person is in a room (spec §5.2)."""
        if not (ch.occupied and (ch.edge or not ch.was_occupied)) or ch.degraded:
            return []
        current = self._room
        if current is not None and self.rooms[current].degraded:
            return []  # the current room cannot see: other rooms are fallback evidence only
        if current is None or self.held:
            return self._move(at, room_slug, "moved")
        if self.home.room(room_slug).exclusive:
            return self._move(at, room_slug, "exclusive")
        if self.home.room(current).exclusive and ch.edge:
            return self._move(at, room_slug, "moved")
        return []  # bleed: the current room still sees someone

    def _move(self, at: datetime, room_slug: str, reason: str) -> list[Transition]:
        previous = self._room if self._presence is Presence.IN_ROOM else None
        self._pending_drop = self._pending_exit = self._fallback_exit = None
        self._quiet_start = None
        spot = self.rooms[room_slug].spot_now()
        out = self._set(at, Presence.IN_ROOM, room_slug, spot, reason)
        if previous != room_slug:
            self._came_from = previous
        self._last_detection = at
        return out

    def _quiet_holds(self) -> bool:
        """In an exclusive room that sees nobody while the room the person came from does."""
        room, back = self._room, self._came_from
        return (
            self._presence is Presence.IN_ROOM
            and room is not None
            and back is not None
            and self._pinned is None
            and self.home.room(room).exclusive
            and not self.rooms[room].occupied
            and self.rooms[back].occupied
            and not self.rooms[back].degraded
        )

    def _update_quiet(self, at: datetime) -> None:
        if not self._quiet_holds():
            self._quiet_start = None
        elif self._quiet_start is None:
            self._quiet_start = at

    def _from_unknown(self, room_slug: str, at: datetime) -> list[Transition]:
        live = {s: r for s, r in self.rooms.items() if r.occupied and not r.degraded}
        target = room_slug if room_slug in live else next(iter(live), None)
        if target is not None:
            spot = self.rooms[target].spot_now()
            return self._set(at, Presence.IN_ROOM, target, spot, "detected")
        if all(not r.degraded for r in self.rooms.values()):
            return self._set(at, Presence.OUT, None, None, "detected")
        return []

    def _in_own_room(self, room_slug: str, ch: SensorChange, at: datetime) -> list[Transition]:
        sensors = self.rooms[room_slug]
        if ch.degraded:
            # The presence sensors went away: hold everything, and let the fallback watch the exits.
            self._pending_drop = None
            if not ch.was_degraded:
                self._reason = "unavailable"
            return []
        back = ch.was_degraded
        if back and sensors.occupied:
            self._fallback_exit = None  # the sensors see someone: no exit happened
        if sensors.occupied:
            if back:
                self._reason = "back"
            out = self._occupied_own_room(room_slug, at)
            if back and not out:
                self._reason = "back"
            return out
        if back:
            # Back and empty: an exit during the outage is either the fallback candidate still
            # waiting for its quiet period, or an exit edge in the usual look-back.
            self._reason = "back"
            if self._fallback_exit is not None:
                return []
        elif not ch.was_occupied:
            return []
        return self._emptied(room_slug, at, "back" if back else "held")

    def _occupied_own_room(self, room_slug: str, at: datetime) -> list[Transition]:
        sensors = self.rooms[room_slug]
        if sensors.occupied:
            if self._pending_exit is not None:
                # Back before the exit was decided: the exit happened, then a short trip ended.
                edge, self._pending_exit = self._pending_exit[0], None
                self._pending_drop = None
                out = self._set(edge, Presence.JUST_LEFT, None, None, "crossing")
                return out + self._set(at, Presence.IN_ROOM, room_slug, sensors.spot_now(), "entry")
            spot = sensors.spot_now()
            if spot is None and self._spot is not None:
                if self._pending_drop is None:
                    self._pending_drop = at
                return []
            self._pending_drop = None
            return self._set(at, Presence.IN_ROOM, room_slug, spot, "spot")
        return []

    def _emptied(self, room_slug: str, at: datetime, held_reason: str) -> list[Transition]:
        """The person's room went empty: another room seen since the person came in wins, then an
        exit crossing (which waits for the move window), else held."""
        self._pending_drop = None
        if self._pinned is None:
            lo = at - self.timings.crossing_window
            if self._room_since is not None and self._room_since > lo:
                lo = self._room_since
            claims = [
                (edge, slug)
                for slug, sensors in self.rooms.items()
                if slug != room_slug and sensors.occupied and not sensors.degraded
                for edge in [sensors.latest_edge(("presence", "spot"), lo, at)]
                if edge is not None
            ]
            if claims:
                return self._move(at, max(claims)[1], "moved")
        edge = self._exit_edge(room_slug, at)
        if edge is None:
            self._reason = held_reason
            return []
        self._pending_exit = (edge, at + self.timings.move_window)
        return []

    # -- fallback (presence sensors unavailable) -------------------------------------------------

    def _fallbacks(self, ch: SensorChange, at: datetime) -> list[Transition]:
        key = (ch.entity_id, ch.at, ch.state)
        if self._fallback_seen == key:
            return []
        if self._presence is Presence.IN_ROOM:
            rooms = [self._room] if self._room and self.rooms[self._room].degraded else []
        elif self._presence in (Presence.OUT, Presence.JUST_LEFT):
            rooms = [r.slug for r in self.home.rooms if r.exits and self.rooms[r.slug].degraded]
        else:
            rooms = []
        out: list[Transition] = []
        for slug in rooms:
            motion, _ = self.fallback_sets(slug)
            if ch.entity_id in motion or ch.entity_id in self.home.room(slug).exits:
                self._fallback_seen = key
                out += self._fallback(slug, ch, at)
        return out

    def _fallback(self, room_slug: str, ch: SensorChange, at: datetime) -> list[Transition]:
        """Exit and entry rules on the exit and motion sensors (spec §5.1, v0.1 §5.3)."""
        timings = self.timings
        room = self.home.room(room_slug)
        motion, others = self.fallback_sets(room_slug)
        if ch.entity_id in room.exits:
            if (
                ch.prev == "on"
                and ch.on_at is not None
                and self._presence is Presence.IN_ROOM
                and self._room == room_slug
                and at - ch.on_at >= timings.exit_door_on_time
                and self._latest(motion, ch.on_at - timings.exit_pre_window, ch.on_at) is not None
            ):
                self._fallback_exit = (room_slug, ch.on_at, at)
            return []
        if not ch.edge or ch.entity_id not in others:
            return []
        if self._fallback_exit is not None and at > self._fallback_exit[2]:
            self._fallback_exit = None
        if self._presence is Presence.IN_ROOM:
            return []
        available = [e for e in others if self._state(e) in ("on", "off")]
        need = min(2, len(available))
        door = self._latest(room.exits, at - timings.entry_window, at)
        if need == 0 or door is None:
            return []
        end = door + timings.entry_window
        seen = {e for e in others if any(door <= x <= end for x in self._edges(e))}
        if len(seen) < need:
            return []
        return self._set(at, Presence.IN_ROOM, room_slug, None, "fallback_entry")

    def _edges(self, entity_id: str) -> list[datetime]:
        owners = self.home.owners(entity_id)
        return self.rooms[owners[0][0]].edges_of(entity_id) if owners else []

    def _state(self, entity_id: str) -> str | None:
        owners = self.home.owners(entity_id)
        return self.rooms[owners[0][0]].state_of(entity_id) if owners else None

    def _latest(self, entities, lo: datetime, hi: datetime) -> datetime | None:
        hits = [x for e in entities for x in self._edges(e) if lo <= x <= hi]
        return max(hits) if hits else None
