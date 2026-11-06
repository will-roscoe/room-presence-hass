"""Sleeping: inferred from being in bed and still, or from the sleep entities all turning off.

:class:`SleepTracker` reads the :class:`~.room.RoomLayer` it is given after the room layer has
seen the same event, so it always judges against the current room state.
"""

from __future__ import annotations

from datetime import datetime

from .room import UNSEEN, RoomLayer
from .types import EngineConfig, RoomState, StateEvent, Sub, Transition


class SleepTracker:
    """Whether the occupant is asleep (spec §5.4).

    Transitions use ``"bed"``/``"sleeping"`` as ``old``/``new``. Reasons: ``sleep_still``,
    ``sleep_entities``, ``wake_movement``, ``wake_entities``, ``wake_left_bed``, ``manual``.
    """

    def __init__(self, cfg: EngineConfig) -> None:
        """:param cfg: entity roles and timings."""
        self.cfg = cfg
        self._sleeping = False
        self._since: datetime | None = None
        self._route: str | None = None
        self._last_move_edge: datetime | None = None
        self._move_on_since: datetime | None = None
        self._move_state: str | None = None
        self._entity_states: dict[str, str] = {}
        #: When the sleep entities last all turned off, until that is used or one turns on.
        self._armed_at: datetime | None = None

    @property
    def sleeping(self) -> bool:
        """True while asleep."""
        return self._sleeping

    @property
    def since(self) -> datetime | None:
        """When the current sleep began (back-dated), else ``None``."""
        return self._since if self._sleeping else None

    def feed(self, ev: StateEvent, room: RoomLayer) -> list[Transition]:
        """Apply one input change (after ``room`` has seen it) and return sleep transitions."""
        cfg = self.cfg
        at = room.now or ev.at
        out: list[Transition] = []
        if ev.entity_id == cfg.movement and ev.state != self._move_state:
            prev, self._move_state = self._move_state, ev.state
            if ev.state == "on" and prev not in UNSEEN:
                self._last_move_edge = self._move_on_since = at
            elif ev.state != "on":
                self._move_on_since = None
        elif ev.entity_id in cfg.sleep_entities:
            was_all_off = self._all_off()
            self._entity_states[ev.entity_id] = ev.state
            if self._all_off() and not was_all_off:
                self._armed_at = at
            elif ev.state == "on":
                self._armed_at = None
                if self._sleeping and self._route == "entities":
                    out += self._wake(at, "wake_entities")
        return out + self.tick(at, room)

    def force(self, at: datetime, sleeping: bool) -> list[Transition]:
        """Set asleep or awake at ``at`` (a ``set_state`` call)."""
        self._armed_at = None
        if sleeping and not self._sleeping:
            return self._sleep(at, "manual", "manual")
        if not sleeping and self._sleeping:
            return self._wake(at, "manual")
        return []

    def next_deadline(self, room: RoomLayer) -> datetime | None:
        """Earliest time :meth:`tick` has work to do, or ``None``."""
        cfg = self.cfg
        if self._sleeping:
            times = []
            if self._move_on_since is not None:
                times.append(self._move_on_since + cfg.wake_on_time)
            if room.state is RoomState.IN_ROOM and room.sub is not Sub.BED and room.sub_since:
                times.append(room.sub_since + cfg.bed_leave_grace)
            return min(times) if times else None
        quiet = self._quiet_start(room)
        return None if quiet is None else quiet + cfg.sleep_quiet

    def tick(self, now: datetime, room: RoomLayer) -> list[Transition]:
        """Apply every sleep or wake rule due at ``now`` and return the transitions."""
        cfg = self.cfg
        if self._sleeping:
            if room.state is not RoomState.IN_ROOM:
                return self._wake(room.since or now, "wake_left_bed")
            if self._move_on_since is not None and now >= self._move_on_since + cfg.wake_on_time:
                return self._wake(self._move_on_since + cfg.wake_on_time, "wake_movement")
            left = room.sub_since
            if room.sub is not Sub.BED and left is not None and now >= left + cfg.bed_leave_grace:
                return self._wake(left, "wake_left_bed")
            return []
        if not self._in_bed(room):
            return []
        if self._armed_at is not None:
            start = max(self._armed_at, room.sub_since or self._armed_at)
            self._armed_at = None
            return self._sleep(start, "entities", "sleep_entities")
        quiet = self._quiet_start(room)
        if quiet is not None and now >= quiet + cfg.sleep_quiet:
            return self._sleep(quiet, "still", "sleep_still")
        return []

    # -- internals ------------------------------------------------------------------------------

    def _all_off(self) -> bool:
        ents = self.cfg.sleep_entities
        return bool(ents) and all(self._entity_states.get(e) == "off" for e in ents)

    @staticmethod
    def _in_bed(room: RoomLayer) -> bool:
        return room.state is RoomState.IN_ROOM and room.sub is Sub.BED

    def _quiet_start(self, room: RoomLayer) -> datetime | None:
        """Start of the current stillness, or ``None`` when the stillness route cannot apply."""
        if self.cfg.movement is None or self._move_state not in ("on", "off"):
            return None
        if not self._in_bed(room) or room.sub_since is None:
            return None
        if self._move_on_since is not None:
            return None
        edge = self._last_move_edge
        return room.sub_since if edge is None else max(edge, room.sub_since)

    def _sleep(self, at: datetime, route: str, reason: str) -> list[Transition]:
        self._sleeping, self._since, self._route = True, at, route
        return [Transition(at, "bed", "sleeping", reason)]

    def _wake(self, at: datetime, reason: str) -> list[Transition]:
        self._sleeping, self._route = False, None
        self._armed_at = None
        return [Transition(at, "sleeping", "bed", reason)]
