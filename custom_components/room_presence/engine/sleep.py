"""Sleeping on one bed spot: in bed and still, or the spot's sleep entities all turning off.

:class:`SleepTracker` reads the :class:`~.person.PersonTracker` it is given after the person
tracker has seen the same event, so it always judges against where the person is now.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .person import PersonTracker
from .rooms import UNSEEN
from .types import Presence, SpotConfig, StateEvent, Timings, Transition

#: How long after the sleep entities all turn off getting into bed still counts as going to sleep.
ENTITY_ARM_WINDOW = timedelta(minutes=5)


class SleepTracker:
    """Whether the person is asleep on one bed spot (spec §5.5, v0.1 §5.4).

    Transitions use ``"bed"``/``"asleep"`` as ``old``/``new``. Reasons: ``sleep_still``,
    ``sleep_entities``, ``wake_movement``, ``wake_entities``, ``wake_left_bed``, ``manual``.
    """

    def __init__(self, room_slug: str, spot: SpotConfig, timings: Timings) -> None:
        """:param room_slug: the room the bed is in.
        :param spot: the bed spot; its ``stillness`` sensor and ``sleep_entities`` drive the rules.
        :param timings: ``sleep_quiet``, ``wake_on_time`` and ``bed_leave_grace``.
        """
        self.room_slug = room_slug
        self.spot = spot
        self.timings = timings
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

    def feed(self, ev: StateEvent, person: PersonTracker) -> list[Transition]:
        """Apply one input change (after ``person`` has seen it) and return sleep transitions."""
        at = person.now or ev.at
        out: list[Transition] = []
        if ev.entity_id == self.spot.stillness and ev.state != self._move_state:
            prev, self._move_state = self._move_state, ev.state
            if ev.state == "on" and prev not in UNSEEN:
                self._last_move_edge = self._move_on_since = at
            elif ev.state != "on":
                self._move_on_since = None
        elif ev.entity_id in self.spot.sleep_entities:
            prev = self._entity_states.get(ev.entity_id)
            self._entity_states[ev.entity_id] = ev.state
            # Only a real on -> off that leaves everything off arms the route; a first sighting
            # or a return from unavailable says nothing about someone going to sleep.
            if prev == "on" and self._all_off():
                self._armed_at = at
            elif ev.state == "on":
                self._armed_at = None
                if self._sleeping and self._route == "entities":
                    out += self._wake(at, "wake_entities")
        return out + self.tick(at, person)

    def force(self, at: datetime, sleeping: bool) -> list[Transition]:
        """Set asleep or awake at ``at`` (a ``set_state`` call)."""
        self._armed_at = None
        if sleeping and not self._sleeping:
            return self._sleep(at, "manual", "manual")
        if not sleeping and self._sleeping:
            return self._wake(at, "manual")
        return []

    def next_deadline(self, person: PersonTracker) -> datetime | None:
        """Earliest time :meth:`tick` has work to do, or ``None``."""
        timings = self.timings
        if self._sleeping:
            times = []
            if self._move_on_since is not None:
                times.append(self._move_on_since + timings.wake_on_time)
            if self._in_room(person) and person.spot != self.spot.slug and person.spot_since:
                times.append(person.spot_since + timings.bed_leave_grace)
            return min(times) if times else None
        quiet = self._quiet_start(person)
        return None if quiet is None else quiet + timings.sleep_quiet

    def tick(self, now: datetime, person: PersonTracker) -> list[Transition]:
        """Apply every sleep or wake rule due at ``now`` and return the transitions."""
        timings = self.timings
        if self._armed_at is not None and (
            not self._in_room(person) or now > self._armed_at + ENTITY_ARM_WINDOW
        ):
            self._armed_at = None
        if self._sleeping:
            if not self._in_room(person):
                return self._wake(person.since or now, "wake_left_bed")
            if (
                self._move_on_since is not None
                and now >= self._move_on_since + timings.wake_on_time
            ):
                return self._wake(self._move_on_since + timings.wake_on_time, "wake_movement")
            left = person.spot_since
            off_bed = person.spot != self.spot.slug and left is not None
            if off_bed and now >= left + timings.bed_leave_grace:
                return self._wake(left, "wake_left_bed")
            return []
        if not self._in_bed(person):
            return []
        if self._armed_at is not None:
            start = max(self._armed_at, person.spot_since or self._armed_at)
            self._armed_at = None
            return self._sleep(start, "entities", "sleep_entities")
        quiet = self._quiet_start(person)
        if quiet is not None and now >= quiet + timings.sleep_quiet:
            return self._sleep(quiet, "still", "sleep_still")
        return []

    # -- internals ------------------------------------------------------------------------------

    def _all_off(self) -> bool:
        ents = self.spot.sleep_entities
        return bool(ents) and all(self._entity_states.get(e) == "off" for e in ents)

    def _in_room(self, person: PersonTracker) -> bool:
        return person.presence is Presence.IN_ROOM and person.room == self.room_slug

    def _in_bed(self, person: PersonTracker) -> bool:
        return self._in_room(person) and person.spot == self.spot.slug

    def _quiet_start(self, person: PersonTracker) -> datetime | None:
        """Start of the current stillness, or ``None`` when the stillness route cannot apply."""
        if self.spot.stillness is None or self._move_state not in ("on", "off"):
            return None
        if not self._in_bed(person) or person.spot_since is None:
            return None
        if self._move_on_since is not None:
            return None
        edge = self._last_move_edge
        return person.spot_since if edge is None else max(edge, person.spot_since)

    def _sleep(self, at: datetime, route: str, reason: str) -> list[Transition]:
        self._sleeping, self._since, self._route = True, at, route
        return [Transition(at, "bed", "asleep", reason)]

    def _wake(self, at: datetime, reason: str) -> list[Transition]:
        self._sleeping, self._route = False, None
        self._armed_at = None
        return [Transition(at, "asleep", "bed", reason)]
