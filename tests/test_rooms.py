"""Per-room sensor tracking: occupancy, spots, degraded, edges and their memory."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from custom_components.room_presence.engine.rooms import RoomSensors
from custom_components.room_presence.engine.types import StateEvent, Timings
from tests.homes import FP2, THP, bedroom

T0 = datetime(2000, 2, 3, 12, 0, tzinfo=UTC)
ROOM, BED, DESK, DOOR = (FP2.format(z) for z in ("room", "bed", "desk", "door"))


def t(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def sensors(room=None, timings=None) -> RoomSensors:
    return RoomSensors(room or bedroom(motion=(THP.format(3),)), timings or Timings())


def feed(rs: RoomSensors, seconds: float, entity: str, state: str):
    return rs.feed(StateEvent(t(seconds), entity, state))


def test_occupied_from_presence_or_spot():
    rs = sensors()
    feed(rs, 0, ROOM, "off")
    assert not rs.occupied
    ch = feed(rs, 1, BED, "on")
    assert rs.occupied
    assert (ch.was_occupied, ch.occupied) == (False, True)
    feed(rs, 2, BED, "off")
    feed(rs, 3, ROOM, "on")
    assert rs.occupied


def test_spot_now_follows_list_order():
    rs = sensors()
    feed(rs, 0, BED, "on")
    feed(rs, 0, DESK, "on")
    assert rs.spot_now() == "bed"
    room = bedroom()
    reordered = replace(room, spots=tuple(reversed(room.spots)))
    rs2 = sensors(reordered)
    feed(rs2, 0, BED, "on")
    feed(rs2, 0, DESK, "on")
    assert rs2.spot_now() == "desk"
    feed(rs2, 1, DESK, "off")
    feed(rs2, 1, BED, "off")
    assert rs2.spot_now() is None


def test_degraded_until_presence_seen():
    rs = sensors()
    assert rs.degraded
    ch = feed(rs, 0, ROOM, "off")
    assert not rs.degraded
    assert (ch.was_degraded, ch.degraded) == (True, False)
    feed(rs, 1, ROOM, "unavailable")
    assert rs.degraded


def test_first_sighting_on_is_edge():
    rs = sensors()
    ch = feed(rs, 0, ROOM, "on")
    assert ch.edge
    assert ch.prev is None
    assert rs.latest_edge(("presence",), t(-1), t(1)) == t(0)


def test_on_after_unavailable_is_not_edge():
    rs = sensors()
    feed(rs, 0, ROOM, "unavailable")
    ch = feed(rs, 5, ROOM, "on")
    assert not ch.edge
    assert rs.latest_edge(("presence",), t(-1), t(10)) is None


def test_repeat_state_returns_none():
    rs = sensors()
    assert feed(rs, 0, ROOM, "on") is not None
    assert feed(rs, 1, ROOM, "on") is None
    assert feed(rs, 2, "binary_sensor.elsewhere", "on") is None


def test_on_at_tracks_the_on_interval():
    rs = sensors()
    feed(rs, 0, DOOR, "off")
    feed(rs, 10, DOOR, "on")
    ch = feed(rs, 19, DOOR, "off")
    assert ch.on_at == t(10)
    ch = feed(rs, 30, DOOR, "on")
    assert ch.on_at is None


def test_entity_in_two_roles_reports_both_kinds():
    rs = sensors()  # THP 3 is an exit and a motion sensor of the bedroom
    ch = feed(rs, 0, THP.format(3), "on")
    assert ch.kinds == frozenset({"exit", "motion"})
    assert rs.latest_edge(("exit",), t(-1), t(1)) == t(0)
    assert rs.latest_edge(("motion",), t(-1), t(1)) == t(0)
    assert rs.latest_edge(("presence",), t(-1), t(1)) is None


def test_first_and_latest_edge():
    rs = sensors()
    for s in (0, 20, 40):
        feed(rs, s, DOOR, "on")
        feed(rs, s + 2, DOOR, "off")
    assert rs.first_edge(("exit",), t(10), t(50)) == t(20)
    assert rs.latest_edge(("exit",), t(10), t(50)) == t(40)
    assert rs.first_edge(("exit",), t(41), t(50)) is None


def test_available_lists_on_off_entities():
    rs = sensors(bedroom(motion=(THP.format(1), THP.format(4))))
    feed(rs, 0, THP.format(1), "off")
    feed(rs, 0, THP.format(4), "unavailable")
    assert rs.available(("motion",)) == [THP.format(1)]


def test_edge_memory_covers_longest_window():
    rs = sensors(timings=Timings(shower_window=timedelta(minutes=40)))
    feed(rs, 0, DOOR, "on")
    feed(rs, 1, DOOR, "off")
    for i in range(1, 40):  # other edges keep arriving, which prunes old ones
        feed(rs, i * 60, DOOR, "on")
        feed(rs, i * 60 + 1, DOOR, "off")
    assert rs.first_edge(("exit",), t(39 * 60 - 39 * 60), t(39 * 60)) == t(0)


def test_state_of():
    rs = sensors()
    assert rs.state_of(ROOM) is None
    feed(rs, 0, ROOM, "off")
    assert rs.state_of(ROOM) == "off"
