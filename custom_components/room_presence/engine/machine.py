"""The presence machine: room layer, sleep and places combined into one ``location``.

.. code-block:: python

    m = PresenceMachine(cfg, places)
    for ev in events:  # StateEvent / ManualEvent, in time order
        for tr in m.feed(ev):
            print(tr.at, tr.old, "->", tr.new, tr.reason)
    m.snapshot().location
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from .places import Place, match_place
from .room import RoomLayer
from .sleep import SleepTracker
from .types import EngineConfig, ManualEvent, RoomState, Snapshot, StateEvent, Sub, Transition

#: Every ``location`` value that is not a place slug.
LOCATIONS_FIXED = (
    "bed",
    "desk",
    "room",
    "bathroom",
    "sleeping",
    "recently_left",
    "away",
    "unknown",
)
_ROOM_SUBS = {"bed": Sub.BED, "desk": Sub.DESK, "room": Sub.ROOM, "bathroom": Sub.BATHROOM}


def tracker_zone(state: str | None) -> str | None:
    """Return the zone entity id in a tracker state (``home`` → ``zone.home``), else ``None``."""
    if state == "home":
        return "zone.home"
    return state if state and state.startswith("zone.") else None


class PresenceMachine:
    """One tracked room's whole state (spec §5).

    :meth:`feed` and :meth:`tick` return **location** transitions: ``old``/``new`` are ``location``
    strings, ``at`` is when the change took effect, ``reason`` comes from the rule that caused it
    (``crossing``, ``sleep_still``, ``phone``, ``manual``, ...).
    """

    def __init__(self, cfg: EngineConfig, places: Sequence[Place]) -> None:
        """:param cfg: entity roles and timings.
        :param places: the configured places; their slugs must not clash with
            :data:`LOCATIONS_FIXED`.
        """
        self.cfg = cfg
        self.places = list(places)
        self.room = RoomLayer(cfg)
        self.sleep = SleepTracker(cfg)
        self._ssid: str | None = None
        self._zone: str | None = None
        self._match: Place | None = None
        self._match_since: datetime | None = None
        #: A ``set_state`` place slug or ``away``, held while the room layer stays out.
        self._manual_place: str | None = None
        self._location = "unknown"
        self._since: datetime | None = None
        self._reason = "start"

    # -- input ----------------------------------------------------------------------------------

    def feed(self, ev: StateEvent | ManualEvent) -> list[Transition]:
        """Apply one event (deadlines up to its time run first) and return location transitions.

        :raises ValueError: a :class:`~.types.ManualEvent` whose state is ``unknown``, not a
            fixed location and not a configured place slug; nothing changes.
        """
        if isinstance(ev, ManualEvent):
            self._check_manual(ev.state)
        out = self.tick(ev.at)
        if isinstance(ev, ManualEvent):
            return out + self._emit(self._manual(ev), ev.at)
        layer = self.room.feed(ev)
        at = self.room.now or ev.at
        if ev.entity_id in (self.cfg.phone_tracker, self.cfg.phone_ssid):
            if ev.entity_id == self.cfg.phone_tracker:
                self._zone = tracker_zone(ev.state)
            else:
                self._ssid = ev.state
            self._update_match(at)
        layer += self._phone_override(at)
        layer += self.sleep.feed(ev, self.room)
        return out + self._emit(layer, at)

    def tick(self, now: datetime) -> list[Transition]:
        """Run every deadline due at or before ``now`` and return location transitions."""
        out: list[Transition] = []
        while (d := self.next_deadline()) is not None and d <= now:
            layer = self.room.tick(d) + self.sleep.tick(d, self.room)
            out += self._emit(layer, d)
        return out

    def next_deadline(self) -> datetime | None:
        """Earliest time :meth:`tick` has work to do, or ``None``."""
        ds = [d for d in (self.room.next_deadline(), self.sleep.next_deadline(self.room)) if d]
        return min(ds) if ds else None

    def snapshot(self) -> Snapshot:
        """Return the current answer (see :class:`~.types.Snapshot`)."""
        room = self.room
        match = self._match
        place = self._manual_place or (match.slug if match is not None else None)
        if place == "away":
            place = None
        return Snapshot(
            location=self._location,
            room_state=room.state,
            sub=room.sub,
            sleeping=self.sleep.sleeping,
            held=room.held,
            degraded=room.degraded,
            place=place,
            conflict=room.detected and match is not None and not match.on_site,
            since=self._since,
            reason=self._reason,
        )

    @property
    def match_since(self) -> datetime | None:
        """When the phone's place match last changed."""
        return self._match_since

    # -- internals ------------------------------------------------------------------------------

    def _check_manual(self, state: str) -> None:
        allowed = set(LOCATIONS_FIXED) - {"unknown"} | {p.slug for p in self.places}
        if state not in allowed:
            raise ValueError(f"unknown state {state!r}; expected one of {sorted(allowed)}")

    def _manual(self, ev: ManualEvent) -> list[Transition]:
        at, state = ev.at, ev.state
        if state in _ROOM_SUBS or state == "sleeping":
            self._manual_place = None
            sub = _ROOM_SUBS.get(state, Sub.BED)
            out = self.room.force(at, RoomState.IN_ROOM, sub, "manual")
            return out + self.sleep.force(at, state == "sleeping")
        if state == "recently_left":
            self._manual_place = None
            out = self.room.force(at, RoomState.RECENTLY_LEFT, None, "manual")
        else:
            self._manual_place = state
            out = self.room.force(at, RoomState.OUT, None, "manual")
        return out + self.sleep.force(at, False)

    def _update_match(self, at: datetime) -> None:
        new = match_place(self.places, self._ssid, self._zone)
        old_slug = self._match.slug if self._match else None
        if (new.slug if new else None) == old_slug:
            return
        self._match, self._match_since = new, at
        if new is not None and self._manual_place not in (None, new.slug):
            self._manual_place = None

    def _phone_override(self, at: datetime) -> list[Transition]:
        room, match = self.room, self._match
        if (
            room.state in (RoomState.IN_ROOM, RoomState.RECENTLY_LEFT)
            and not room.detected
            and match is not None
            and not match.on_site
            and self._match_since is not None
            and (room.last_detection is None or self._match_since > room.last_detection)
        ):
            return self.room.force(at, RoomState.OUT, None, "phone")
        return []

    def _compute(self) -> str:
        room = self.room
        if room.state is RoomState.IN_ROOM:
            self._manual_place = None
            if self.sleep.sleeping:
                return "sleeping"
            return room.sub.value if room.sub is not None else "room"
        if room.state is RoomState.RECENTLY_LEFT:
            self._manual_place = None
            return "recently_left"
        if self._manual_place is not None:
            return self._manual_place
        if self._match is not None:
            return self._match.slug
        return "unknown" if room.state is RoomState.UNKNOWN else "away"

    def _emit(self, layer: list[Transition], at: datetime) -> list[Transition]:
        new = self._compute()
        if layer:
            at, reason = layer[-1].at, layer[-1].reason
        else:
            reason = "phone" if new != self._location else self._reason
        if new == self._location:
            return []
        old, self._location, self._since, self._reason = self._location, new, at, reason
        return [Transition(at, old, new, reason)]
