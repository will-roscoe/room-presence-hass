"""The home machine: rooms, the person, sleep, showers, the phone and places, as one status.

.. code-block:: python

    m = HomeMachine(home, places)
    for ev in events:  # StateEvent / ManualEvent, in time order
        for tr in m.feed(ev):
            print(tr.at, tr.old, "->", tr.new, tr.reason)
    m.snapshot().key  # e.g. "bed:asleep", "desk", "just_left", "uni_of_lancashire", "away"
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from .person import PersonTracker
from .places import MEANINGLESS_SSIDS, Place
from .rooms import RoomSensors
from .shower import ShowerTracker
from .sleep import SleepTracker
from .types import (
    Activity,
    HomeConfig,
    ManualEvent,
    Presence,
    Snapshot,
    StateEvent,
    Transition,
    status_key,
)

#: ``set_state`` activities.
MANUAL_ACTIVITIES = ("asleep", "awake")


def _zone_of(state: str | None) -> str | None:
    """The zone entity id in a person state (``home`` → ``zone.home``), else ``None``."""
    if state == "home":
        return "zone.home"
    return state if state and state.startswith("zone.") else None


def _clean_ssid(ssid: str | None) -> str | None:
    if ssid is None:
        return None
    folded = ssid.strip().casefold()
    return None if folded in MEANINGLESS_SSIDS else folded


class HomeMachine:
    """The whole state of one home with one tracked person (spec §5).

    :meth:`feed` and :meth:`tick` return transitions of the status key (see
    :func:`~.types.status_key`): ``at`` is when the change took effect, ``reason`` the rule that
    caused it (``entry``, ``crossing``, ``moved``, ``sleep_still``, ``shower_start``, ``phone``,
    ``manual``, ...). Read :meth:`snapshot` for the full answer.
    """

    def __init__(self, home: HomeConfig, places: Sequence[Place]) -> None:
        """:param home: rooms, person, home evidence and timings.
        :param places: the configured places; their slugs must not clash with room, spot or fixed
            location slugs.
        """
        self.home = home
        self.places = list(places)
        timings = home.timings
        self.rooms = {r.slug: RoomSensors(r, timings) for r in home.rooms}
        self.person = PersonTracker(home, self.rooms)
        self.sleeps = [
            SleepTracker(r.slug, s, timings) for r in home.rooms for s in r.spots if s.role == "bed"
        ]
        self.showers = {
            r.slug: ShowerTracker(r, timings) for r in home.rooms if r.bathroom and r.humidity
        }
        self._spot_room = {s.slug: r.slug for r in home.rooms for s in r.spots}
        self._now: datetime | None = None
        self._zone: str | None = None
        self._zone_since: datetime | None = None
        self._person_seen = False
        self._ssids: dict[str, str] = {}
        self._ssid_home_since: datetime | None = None
        self._match: Place | None = None
        self._match_since: datetime | None = None
        #: A ``set_state`` place slug or ``away``, held until newer evidence.
        self._manual_place: str | None = None
        #: Time of the latest ``set_state`` to away or a place; home evidence must be newer.
        self._manual_out_at: datetime | None = None
        self._key = "unknown"
        self._since: datetime | None = None
        self._reason = "start"

    # -- input ----------------------------------------------------------------------------------

    def feed(self, ev: StateEvent | ManualEvent) -> list[Transition]:
        """Apply one event (deadlines up to its time run first) and return status transitions.

        An event older than the latest one seen is applied at the latest time.

        :raises ValueError: a :class:`~.types.ManualEvent` that :meth:`check_manual` rejects;
            nothing changes.
        """
        if isinstance(ev, ManualEvent):
            self.check_manual(ev.location, ev.activity)
        else:
            for shower in self.showers.values():
                shower.observe(ev)
        out = self.tick(ev.at)
        at = self._advance(ev.at)
        if isinstance(ev, ManualEvent):
            return out + self._emit(self._manual(at, ev), at)
        if at != ev.at:
            ev = StateEvent(at, ev.entity_id, ev.state)
        layer: list[Transition] = []
        exits: set[str] = set()
        for slug, _ in self.home.owners(ev.entity_id):
            ch = self.rooms[slug].feed(ev)
            if ch is None:
                continue
            layer += self.person.on_change(slug, ch)
            if "exit" in ch.kinds and ch.edge:
                exits.add(slug)
        layer += self._feed_showers(ev, exits)
        if ev.entity_id == self.home.person:
            self._on_person(at, ev.state)
        elif ev.entity_id in self.home.ssid_sensors:
            self._on_ssid(at, ev.entity_id, ev.state)
        layer += self._phone_override(at)
        for sleep in self.sleeps:
            layer += sleep.feed(ev, self.person)
        return out + self._emit(layer, at)

    def tick(self, now: datetime) -> list[Transition]:
        """Run every deadline due at or before ``now`` and return status transitions."""
        out: list[Transition] = []
        while (d := self.next_deadline()) is not None and d <= now:
            self._advance(d)
            layer = self.person.tick(d)
            for shower in self.showers.values():
                ended = shower.tick(d)
                if ended:
                    self.person.unpin(d)
                layer += ended
            for sleep in self.sleeps:
                layer += sleep.tick(d, self.person)
            out += self._emit(layer, d)
        self.person.tick(now)
        return out

    def next_deadline(self) -> datetime | None:
        """Earliest time :meth:`tick` has work to do, or ``None``."""
        times = [self.person.next_deadline()]
        times += [s.next_deadline() for s in self.showers.values()]
        times += [s.next_deadline(self.person) for s in self.sleeps]
        ds = [d for d in times if d is not None]
        return min(ds) if ds else None

    def check_manual(self, location: str | None, activity: str | None) -> None:
        """Raise :class:`ValueError` unless ``location``/``activity`` is a valid ``set_state``.

        ``location``: a room or spot slug, ``just_left``, ``away`` or a place slug; ``activity``:
        ``asleep`` or ``awake``; at least one of the two.
        """
        if location is None and activity is None:
            raise ValueError("set_state needs a location or an activity")
        allowed = {*self.rooms, *self._spot_room, "just_left", "away"} | {
            p.slug for p in self.places
        }
        if location is not None and location not in allowed:
            raise ValueError(f"unknown location {location!r}; expected one of {sorted(allowed)}")
        if activity is not None and activity not in MANUAL_ACTIVITIES:
            raise ValueError(f"unknown activity {activity!r}; expected asleep or awake")
        if activity == "asleep" and not self.sleeps:
            raise ValueError("no room has a bed spot")

    def names(self) -> dict[str, str]:
        """Return slug → name for every room, spot, place and named zone."""
        out = {zone.split(".", 1)[1]: name for zone, name in self.home.zone_names.items()}
        out |= {p.slug: p.name for p in self.places}
        for room in self.home.rooms:
            out[room.slug] = room.name
            out |= {s.slug: s.name for s in room.spots}
        return out

    def snapshot(self) -> Snapshot:
        """Return the current answer (see :class:`~.types.Snapshot`)."""
        location, activity = self._location(), self._activity()
        place = self._manual_place if self._manual_place != "away" else None
        if place is None and self._match is not None:
            place = self._match.slug
        return Snapshot(
            key=status_key(location, activity, self.person.presence),
            location=location,
            location_name=self._name(location),
            activity=activity,
            presence=self.person.presence,
            room=self.person.room,
            spot=self.person.spot,
            held=self.person.held,
            home=self._home_evidence(),
            asleep=activity is Activity.ASLEEP,
            showering=activity is Activity.SHOWERING,
            degraded=frozenset(s for s, r in self.rooms.items() if r.degraded),
            place=place,
            conflict=self.person.detected and self._offsite_since() is not None,
            since=self._since,
            reason=self._reason,
        )

    # -- internals ------------------------------------------------------------------------------

    def _advance(self, at: datetime) -> datetime:
        if self._now is None or at > self._now:
            self._now = at
        return self._now

    def _feed_showers(self, ev: StateEvent, exits: set[str]) -> list[Transition]:
        out: list[Transition] = []
        for slug, shower in self.showers.items():
            if slug in exits and shower.showering:
                out += shower.end(ev.at, "exit")
                self.person.unpin(ev.at)
                continue
            was = shower.showering
            trs = shower.feed(ev, self.rooms[slug])
            if trs and was and not shower.showering:
                self.person.unpin(ev.at)
            if shower.showering and not was:
                out += self.person.pin(ev.at, slug)
            out += trs
        return out

    def _on_person(self, at: datetime, state: str) -> None:
        self._person_seen = state not in ("unavailable", "unknown")
        zone = _zone_of(state)
        if zone == self._zone:
            return
        self._zone, self._zone_since = zone, at
        if zone is not None:
            self._manual_place = None
        self._update_match(at)

    def _on_ssid(self, at: datetime, entity_id: str, state: str) -> None:
        was_home = self._ssid_home()
        self._ssids[entity_id] = state
        is_home = self._ssid_home()
        if is_home and not was_home:
            self._ssid_home_since = at
            self._manual_place = None
        elif not is_home:
            self._ssid_home_since = None
        self._update_match(at)

    def _ssid_home(self) -> bool:
        home = {h.strip().casefold() for h in self.home.home_ssids}
        return any(_clean_ssid(s) in home for s in self._ssids.values() if _clean_ssid(s))

    def _best_match(self) -> Place | None:
        ssids = [s for s in self._ssids.values() if _clean_ssid(s)] or [None]
        for place in sorted(self.places, key=lambda p: p.priority):
            for ssid in ssids:
                by_ssid = ssid is not None and ssid in place.ssids
                by_zone = self._zone is not None and self._zone in place.zones
                if (by_ssid and by_zone) if place.mode == "all" else (by_ssid or by_zone):
                    return place
        return None

    def _update_match(self, at: datetime) -> None:
        new = self._best_match()
        if (new.slug if new else None) == (self._match.slug if self._match else None):
            return
        self._match, self._match_since = new, at
        if new is not None and self._manual_place not in (None, new.slug):
            self._manual_place = None

    def _offsite_since(self) -> datetime | None:
        """When the current positive off-site evidence (a place, or a zone that is not home)
        began; ``None`` without any."""
        times = []
        if self._match is not None and self._match_since is not None:
            times.append(self._match_since)
        if self._zone is not None and self._zone != self.home.home_zone and self._zone_since:
            times.append(self._zone_since)
        return max(times) if times else None

    def _cutoff(self) -> datetime | None:
        times = [t for t in (self.person.exit_at, self._manual_out_at) if t is not None]
        return max(times) if times else None

    def _home_evidence(self) -> bool:
        if self.person.presence in (Presence.IN_ROOM, Presence.JUST_LEFT):
            return True
        cutoff = self._cutoff()
        zone_home = self._zone == self.home.home_zone and (
            cutoff is None or (self._zone_since is not None and self._zone_since > cutoff)
        )
        ssid_home = self._ssid_home_since is not None and (
            cutoff is None or self._ssid_home_since > cutoff
        )
        return zone_home or ssid_home

    def _phone_override(self, at: datetime) -> list[Transition]:
        person = self.person
        offsite = self._offsite_since()
        if (
            person.presence in (Presence.IN_ROOM, Presence.JUST_LEFT)
            and not person.detected
            and offsite is not None
            and (person.last_detection is None or offsite > person.last_detection)
        ):
            return person.force(at, Presence.OUT, None, None, "phone")
        return []

    def _location(self) -> str:
        person = self.person
        if person.presence is Presence.IN_ROOM and person.room is not None:
            self._manual_place = None
            return person.spot or person.room
        if person.presence is Presence.JUST_LEFT:
            return "home"
        if self._manual_place is not None:
            return self._manual_place
        if self._home_evidence():
            return "home"
        if self._match is not None:
            return self._match.slug
        if self._zone is not None and self._zone != self.home.home_zone:
            return self._zone.split(".", 1)[1]
        has_info = (
            self._zone is not None
            or self._person_seen
            or any(_clean_ssid(s) for s in self._ssids.values())
        )
        if person.presence is Presence.UNKNOWN and not has_info:
            return "unknown"
        return "away"

    def _activity(self) -> Activity:
        if any(s.sleeping for s in self.sleeps):
            return Activity.ASLEEP
        if any(s.showering for s in self.showers.values()):
            return Activity.SHOWERING
        location = self._location()
        if location == "unknown":
            return Activity.UNKNOWN
        if location == "home" or self.person.presence is Presence.IN_ROOM:
            return Activity.AWAKE
        return Activity.AWAY

    def _name(self, location: str) -> str | None:
        if location in ("home", "away", "unknown"):
            return None
        return self.names().get(location, location)

    def _manual(self, at: datetime, ev: ManualEvent) -> list[Transition]:
        person, out = self.person, []
        for shower in self.showers.values():
            out += shower.end(at, "manual")
        person.unpin(at)
        location, activity = ev.location, ev.activity
        if location is None and activity == "asleep":
            sleep = self.sleeps[0]
            if not (person.room == sleep.room_slug and person.spot == sleep.spot.slug):
                location = sleep.spot.slug
        if location is not None:
            out += self._manual_location(at, location)
        if activity == "asleep":
            sleep = next((s for s in self.sleeps if s.spot.slug == person.spot), self.sleeps[0])
            out += sleep.force(at, True)
        elif activity == "awake" or location is not None:
            for sleep in self.sleeps:
                out += sleep.force(at, False)
        return out

    def _manual_location(self, at: datetime, location: str) -> list[Transition]:
        person = self.person
        if location in self.rooms:
            self._manual_place = None
            return person.force(at, Presence.IN_ROOM, location, None, "manual")
        if location in self._spot_room:
            self._manual_place = None
            return person.force(at, Presence.IN_ROOM, self._spot_room[location], location, "manual")
        if location == "just_left":
            self._manual_place = None
            return person.force(at, Presence.JUST_LEFT, None, None, "manual")
        self._manual_place, self._manual_out_at = location, at
        return person.force(at, Presence.OUT, None, None, "manual")

    def _emit(self, layer: list[Transition], at: datetime) -> list[Transition]:
        out: list[Transition] = []
        if self.person.presence is not Presence.JUST_LEFT:
            # An exit and a return inside one event (a trip shorter than the move window) still
            # shows the exit.
            for tr in layer:
                if tr.new == Presence.JUST_LEFT.value:
                    out += self._emit_key("just_left", tr.at, tr.reason)
        new = self.snapshot().key
        if layer:
            at, reason = layer[-1].at, layer[-1].reason
        else:
            reason = "phone"
        return out + self._emit_key(new, at, reason)

    def _emit_key(self, new: str, at: datetime, reason: str) -> list[Transition]:
        if new == self._key:
            return []
        if self._since is not None and at < self._since:
            at = self._since  # back-dated, but never before the previous change
        old, self._key, self._since, self._reason = self._key, new, at, reason
        return [Transition(at, old, new, reason)]
