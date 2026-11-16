"""Value types of the home engine: configuration, events, transitions and the snapshot.

A :class:`HomeConfig` describes the home: its rooms (each a :class:`RoomConfig` with sensors and
:class:`SpotConfig` spots), the tracked person and the timings. The engine is fed
:class:`StateEvent` and :class:`ManualEvent` objects and answers with :class:`Transition` lists and
a :class:`Snapshot`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

#: Location values that are not a room, spot, place or zone.
FIXED_LOCATIONS = ("home", "away", "unknown")
#: Slugs no room, spot or place may take: the fixed locations and the ``just_left`` status.
RESERVED_SLUGS = (*FIXED_LOCATIONS, "just_left")
#: Sensor roles inside a room, as reported by :meth:`HomeConfig.owners`.
KINDS = frozenset({"presence", "spot", "exit", "motion", "humidity", "stillness", "sleep"})


class Presence(StrEnum):
    """Where the person is: in a room, just left through an exit, out, or unknown."""

    IN_ROOM = "in_room"
    JUST_LEFT = "just_left"
    OUT = "out"
    UNKNOWN = "unknown"


class Activity(StrEnum):
    """What the person is doing: the fixed presets."""

    ASLEEP = "asleep"
    SHOWERING = "showering"
    AWAKE = "awake"
    AWAY = "away"
    UNKNOWN = "unknown"


def _s(seconds: float) -> timedelta:
    return field(default=timedelta(seconds=seconds))


@dataclass(frozen=True)
class Timings:
    """Every timing rule's duration (and the shower threshold), with the spec's defaults."""

    crossing_window: timedelta = _s(60)
    move_window: timedelta = _s(30)
    recently_left_duration: timedelta = _s(300)
    sleep_quiet: timedelta = _s(1800)
    wake_on_time: timedelta = _s(8)
    bed_leave_grace: timedelta = _s(20)
    exit_pre_window: timedelta = _s(180)
    exit_quiet: timedelta = _s(180)
    exit_door_on_time: timedelta = _s(8)
    entry_window: timedelta = _s(90)
    exclusive_quiet: timedelta = _s(120)
    shower_window: timedelta = _s(900)
    shower_end: timedelta = _s(180)
    #: Humidity points above the window's lowest reading that start a shower.
    shower_rise: float = 6.0
    entry_needs_crossing: bool = False

    @property
    def longest_lookback(self) -> timedelta:
        """How long sensor edges are kept: the longest look-back window, at least 15 min."""
        return max(
            self.crossing_window,
            self.entry_window,
            self.exit_pre_window,
            self.shower_window,
            timedelta(minutes=15),
        )


@dataclass(frozen=True)
class SpotConfig:
    """A spot inside a room (bed, desk, sofa) seen by one ``sensor``.

    ``role`` is ``None`` or ``"bed"``; a bed spot detects sleep from its ``stillness`` (movement)
    sensor and its ``sleep_entities`` (e.g. lights) all turning off.
    """

    slug: str
    name: str
    sensor: str
    role: str | None = None
    stillness: str | None = None
    sleep_entities: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoomConfig:
    """One room and its sensors.

    ``presence`` sensors see still people (at least one is required); ``motion`` sensors only see
    movement and are the fallback when every presence sensor is unavailable; ``exits`` fire when
    someone passes the room's door to outside the home. ``spots`` are in priority order.
    ``bathroom`` with a ``humidity`` sensor enables showering; ``exclusive`` means the room's
    presence sensors see only this room, so their edges override other rooms.
    """

    slug: str
    name: str
    presence: tuple[str, ...]
    motion: tuple[str, ...] = ()
    exits: tuple[str, ...] = ()
    spots: tuple[SpotConfig, ...] = ()
    bathroom: bool = False
    exclusive: bool = False
    humidity: str | None = None

    def roles(self) -> list[tuple[str, str]]:
        """Return ``(entity_id, kind)`` for every sensor of this room, in configuration order."""
        out = [(e, "presence") for e in self.presence]
        for spot in self.spots:
            out.append((spot.sensor, "spot"))
            if spot.stillness:
                out.append((spot.stillness, "stillness"))
            out += [(e, "sleep") for e in spot.sleep_entities]
        out += [(e, "exit") for e in self.exits]
        out += [(e, "motion") for e in self.motion]
        if self.humidity:
            out.append((self.humidity, "humidity"))
        return out


@dataclass(frozen=True)
class HomeConfig:
    """The home: rooms, the tracked person, home evidence and timings.

    ``person`` is a ``person`` entity id; ``home_zone`` the zone that means home; ``home_ssids`` the
    Wi-Fi names that mean home, reported by any of ``ssid_sensors``; ``zone_names`` maps zone
    entity ids to their friendly names.

    :raises ValueError: a room without presence sensors; a room or spot slug in
        :data:`RESERVED_SLUGS`; a slug used twice across rooms and spots; ``humidity`` on a room
        that is not a bathroom.
    """

    rooms: tuple[RoomConfig, ...]
    person: str | None = None
    home_zone: str = "zone.home"
    home_ssids: frozenset[str] = frozenset()
    ssid_sensors: tuple[str, ...] = ()
    zone_names: dict[str, str] = field(default_factory=dict, compare=False, hash=False)
    timings: Timings = field(default_factory=Timings)
    _owners: dict[str, tuple[tuple[str, frozenset[str]], ...]] = field(
        init=False, repr=False, compare=False, hash=False
    )

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for room in self.rooms:
            if not room.presence:
                raise ValueError(f"room {room.slug!r} has no presence sensor")
            if room.humidity and not room.bathroom:
                raise ValueError(f"room {room.slug!r} has a humidity sensor but is not a bathroom")
            for slug in (room.slug, *(s.slug for s in room.spots)):
                if slug in RESERVED_SLUGS:
                    raise ValueError(f"slug {slug!r} is reserved")
                if slug in seen:
                    raise ValueError(f"slug {slug!r} is used twice")
                seen.add(slug)
        owners: dict[str, dict[str, set[str]]] = {}
        for room in self.rooms:
            for entity_id, kind in room.roles():
                owners.setdefault(entity_id, {}).setdefault(room.slug, set()).add(kind)
        object.__setattr__(
            self,
            "_owners",
            {
                e: tuple((r, frozenset(k)) for r, k in by_room.items())
                for e, by_room in owners.items()
            },
        )

    def room(self, slug: str) -> RoomConfig:
        """Return the room with ``slug``.

        :raises KeyError: no such room.
        """
        for room in self.rooms:
            if room.slug == slug:
                return room
        raise KeyError(slug)

    def owners(self, entity_id: str) -> tuple[tuple[str, frozenset[str]], ...]:
        """Return ``(room slug, kinds)`` for every room that uses ``entity_id``; empty if none."""
        return self._owners.get(entity_id, ())

    def inputs(self) -> tuple[str, ...]:
        """Return every entity the engine reads: room sensors, the person, then SSID sensors."""
        ids = [e for room in self.rooms for e, _ in room.roles()]
        if self.person:
            ids.append(self.person)
        ids += self.ssid_sensors
        return tuple(dict.fromkeys(ids))


@dataclass(frozen=True)
class StateEvent:
    """An input entity changed to ``state`` at ``at`` (UTC-aware)."""

    at: datetime
    entity_id: str
    state: str


@dataclass(frozen=True)
class ManualEvent:
    """A ``set_state`` call taking effect at ``at``: a location and/or an activity to force."""

    at: datetime
    location: str | None = None
    activity: str | None = None


@dataclass(frozen=True)
class Transition:
    """One state change; ``at`` is when it took effect, which may be before it was seen."""

    at: datetime
    old: str
    new: str
    reason: str


def status_key(location: str, activity: Activity, presence: Presence) -> str:
    """Return the status key: ``just_left`` while just left, else ``location`` plus
    ``:asleep``/``:showering`` when the activity is one of those (``bed:asleep``, ``desk``)."""
    if presence is Presence.JUST_LEFT:
        return "just_left"
    if activity in (Activity.ASLEEP, Activity.SHOWERING):
        return f"{location}:{activity.value}"
    return location


@dataclass(frozen=True)
class Snapshot:
    """The engine's current answer.

    ``key`` is the status key (:func:`status_key`); ``location`` a spot, room, place or zone slug
    or one of :data:`FIXED_LOCATIONS`; ``location_name`` the configured name of that spot, room,
    place or zone (``None`` for a fixed location). ``room``/``spot`` are where the person is while
    ``presence`` is ``in_room``; ``held`` means in a room with nothing detecting now; ``home`` means
    at home by any evidence; ``degraded`` holds the slugs of rooms whose presence sensors are all
    unavailable; ``place`` the active place slug; ``conflict`` detected in a room while the phone
    is off-site; ``since``/``reason`` say when and why ``key`` last changed.
    """

    key: str
    location: str
    location_name: str | None
    activity: Activity
    presence: Presence
    room: str | None
    spot: str | None
    held: bool
    home: bool
    asleep: bool
    showering: bool
    degraded: frozenset[str]
    place: str | None
    conflict: bool
    since: datetime | None
    reason: str
