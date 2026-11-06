"""Value types shared by the presence engine: states, configuration, events and outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum


class RoomState(StrEnum):
    """Room layer state: in the room, just left through the door, out, or nothing known yet."""

    IN_ROOM = "in_room"
    RECENTLY_LEFT = "recently_left"
    OUT = "out"
    UNKNOWN = "unknown"


class Sub(StrEnum):
    """Where in the room, while the room layer is ``in_room``."""

    BED = "bed"
    DESK = "desk"
    ROOM = "room"
    BATHROOM = "bathroom"


def _s(seconds: float) -> timedelta:
    return field(default=timedelta(seconds=seconds))


@dataclass(frozen=True)
class EngineConfig:
    """Entity roles and timings for one tracked room.

    Every role except ``fp2_room`` is optional: ``None`` or an empty tuple disables the rules that
    use it. Timings are :class:`~datetime.timedelta`; their defaults are the spec's.
    """

    fp2_room: str
    fp2_bed: str | None = None
    fp2_desk: str | None = None
    door_witnesses: tuple[str, ...] = ()
    bathroom: str | None = None
    movement: str | None = None
    fallback_motion: tuple[str, ...] = ()
    sleep_entities: tuple[str, ...] = ()
    phone_tracker: str | None = None
    phone_ssid: str | None = None
    crossing_window: timedelta = _s(60)
    bathroom_window: timedelta = _s(30)
    recently_left_duration: timedelta = _s(300)
    sleep_quiet: timedelta = _s(1800)
    wake_on_time: timedelta = _s(8)
    bed_leave_grace: timedelta = _s(20)
    entry_needs_crossing: bool = False
    exit_pre_window: timedelta = _s(180)
    exit_quiet: timedelta = _s(180)
    exit_door_on_time: timedelta = _s(8)
    entry_window: timedelta = _s(90)


@dataclass(frozen=True)
class StateEvent:
    """An input entity changed to ``state`` at ``at`` (UTC-aware)."""

    at: datetime
    entity_id: str
    state: str


@dataclass(frozen=True)
class ManualEvent:
    """A ``set_state`` call taking effect at ``at``: a fixed location or a place slug."""

    at: datetime
    state: str


@dataclass(frozen=True)
class Transition:
    """One state change; ``at`` is when it took effect, which may be before it was seen."""

    at: datetime
    old: str
    new: str
    reason: str


@dataclass(frozen=True)
class Snapshot:
    """The engine's current answer.

    ``location`` is the published state; ``room_state`` and ``sub`` are the room layer's view;
    ``held`` means in the room with nothing detecting now; ``place`` is the active place slug (or
    ``None``); ``conflict`` means detected while the phone matches an off-site place; ``since`` is
    when ``location`` last changed (back-dated where the rule says so); ``reason`` explains it.
    """

    location: str
    room_state: RoomState
    sub: Sub | None
    sleeping: bool
    held: bool
    degraded: bool
    place: str | None
    conflict: bool
    since: datetime | None
    reason: str
