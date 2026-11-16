"""Person tracker, single room: spots, crossings, holding, entry, just left."""

import pytest

from custom_components.room_presence.engine.rooms import ZONE_SETTLE
from custom_components.room_presence.engine.types import Presence
from tests.homes import FP2, THP, bedroom, home
from tests.rig import Rig, t

ROOM, BED, DESK, DOOR = (FP2.format(z) for z in ("room", "bed", "desk", "door"))
THP_DOOR = THP.format(3)
HOME = home(bedroom())


def in_room(h=HOME) -> Rig:
    rig = Rig(h)
    rig.run((0, ROOM, "on"))
    assert rig.person.presence is Presence.IN_ROOM
    return rig


def just_left(h=HOME) -> Rig:
    rig = in_room(h)
    rig.run((40, THP_DOOR, "on"), (45, THP_DOOR, "off"), (70, ROOM, "off"))
    rig.tick(100)
    assert rig.person.presence is Presence.JUST_LEFT
    return rig


def out() -> Rig:
    rig = Rig(HOME)
    rig.run((0, ROOM, "off"))
    assert rig.person.presence is Presence.OUT
    return rig


def test_starts_unknown():
    assert Rig(HOME).person.presence is Presence.UNKNOWN


def test_spot_prefers_list_order_and_drop_waits():
    rig = in_room()
    assert rig.view == "bedroom"
    rig.run((10, BED, "on"))
    assert rig.view == "bedroom/bed"
    rig.run((20, DESK, "on"))
    assert rig.view == "bedroom/bed"
    rig.run((30, BED, "off"))
    assert rig.view == "bedroom/desk"
    rig.run((40, DESK, "off"))
    assert rig.view == "bedroom/desk"  # dropping to no spot waits for the sensors to settle
    assert rig.person.next_deadline() == t(40) + ZONE_SETTLE
    trs = rig.person.tick(t(40) + ZONE_SETTLE)
    assert rig.view == "bedroom"
    assert [(tr.at, tr.old, tr.new) for tr in trs] == [(t(40), "bedroom/desk", "bedroom")]


def test_empty_without_crossing_holds_last_spot():
    rig = in_room()
    trs = rig.run((1, BED, "on"), (100, BED, "off"), (100, ROOM, "off"))
    assert rig.person.presence is Presence.IN_ROOM
    assert rig.view == "bedroom/bed"
    assert rig.person.held is True
    assert rig.person.detected is False
    assert rig.person.reason == "held"
    assert not [tr for tr in trs if tr.reason == "crossing"]


def test_empty_after_crossing_is_just_left_backdated():
    rig = in_room()
    rig.run((40, THP_DOOR, "on"), (45, THP_DOOR, "off"), (70, ROOM, "off"))
    trs = rig.tick(100)
    assert rig.person.presence is Presence.JUST_LEFT
    assert [(tr.at, tr.old, tr.new, tr.reason) for tr in trs] == [
        (t(40), "bedroom", "just_left", "crossing")
    ]
    assert rig.person.since == t(40)
    assert rig.person.exit_at == t(40)


def test_exit_decided_after_move_window():
    rig = in_room()
    rig.run((50, DOOR, "on"), (55, DOOR, "off"), (70, ROOM, "off"))
    assert rig.person.presence is Presence.IN_ROOM
    assert rig.person.next_deadline() == t(100)
    assert rig.tick(99) == []
    trs = rig.tick(100)
    assert [(tr.at, tr.new, tr.reason) for tr in trs] == [(t(50), "just_left", "crossing")]


def test_back_in_room_before_decision_cancels_exit():
    rig = in_room()
    rig.run((50, DOOR, "on"), (55, DOOR, "off"), (70, ROOM, "off"), (80, ROOM, "on"))
    rig.tick(200)
    assert rig.person.presence is Presence.IN_ROOM


def test_exit_edge_older_than_window_is_not_a_crossing():
    rig = in_room()
    rig.run((9, DOOR, "on"), (12, DOOR, "off"), (70, ROOM, "off"))
    rig.tick(200)
    assert rig.person.presence is Presence.IN_ROOM
    assert rig.person.held is True


def test_exit_sensor_alone_is_not_entry():
    rig = out()
    rig.run((10, DOOR, "on"), (20, DOOR, "off"))
    assert rig.person.presence is Presence.OUT


def test_presence_is_entry_and_short_trip():
    rig = just_left()
    trs = rig.run((120, ROOM, "on"))
    assert rig.view == "bedroom"
    assert [(tr.old, tr.new, tr.reason) for tr in trs] == [("just_left", "bedroom", "entry")]


def test_entry_needs_crossing_option():
    rig = Rig(home(bedroom(bed=False, desk=False, exits=(DOOR,)), entry_needs_crossing=True))
    rig.run((0, ROOM, "off"))
    rig.run((10, ROOM, "on"))
    assert rig.person.presence is Presence.OUT
    rig.run((20, ROOM, "off"), (100, DOOR, "on"), (130, ROOM, "on"))
    assert rig.person.presence is Presence.IN_ROOM


def test_entry_needs_crossing_does_not_apply_from_unknown():
    rig = Rig(home(bedroom(bed=False, desk=False, exits=(DOOR,)), entry_needs_crossing=True))
    rig.run((0, ROOM, "on"))
    assert rig.person.presence is Presence.IN_ROOM


def test_just_left_expires_to_out_after_5_min_from_backdated_start():
    rig = just_left()
    assert rig.person.next_deadline() == t(340)
    assert rig.tick(339) == []
    trs = rig.tick(340)
    assert rig.person.presence is Presence.OUT
    assert [(tr.at, tr.old, tr.new, tr.reason) for tr in trs] == [
        (t(340), "just_left", "out", "just_left_expired")
    ]
    assert rig.person.exit_at == t(40)


@pytest.mark.parametrize("first", [BED, ROOM])
def test_same_second_sensor_changes(first):
    rig = in_room()
    rig.run((5, BED, "on"))
    second = ROOM if first == BED else BED
    trs = rig.run((100, first, "off"), (100, second, "off"))
    assert rig.view == "bedroom/bed"
    assert rig.person.held is True
    assert trs == []


def test_sensor_lag_of_a_few_seconds_still_holds_bed():
    rig = in_room()
    rig.run((5, BED, "on"))
    trs = rig.run((100, BED, "off"), (101, ROOM, "off"))
    assert rig.view == "bedroom/bed"
    assert trs == []


def test_unavailable_exit_sensor_is_ignored():
    rig = in_room()
    rig.run((1, THP_DOOR, "unavailable"), (40, DOOR, "on"), (70, ROOM, "off"))
    rig.tick(100)
    assert rig.person.presence is Presence.JUST_LEFT
    rig = in_room()
    rig.run(
        (1, THP_DOOR, "unavailable"),
        (1, DOOR, "unavailable"),
        (60, THP_DOOR, "on"),  # unavailable -> on is not an edge
        (70, ROOM, "off"),
    )
    rig.tick(200)
    assert rig.person.presence is Presence.IN_ROOM
    assert rig.person.held is True


def test_unknown_then_empty_is_out():
    rig = Rig(HOME)
    trs = rig.run((0, ROOM, "off"))
    assert rig.person.presence is Presence.OUT
    assert [(tr.old, tr.new, tr.reason) for tr in trs] == [("unknown", "out", "detected")]


def test_last_detection_tracks_the_room():
    rig = in_room()
    rig.run((5, BED, "on"), (50, BED, "off"), (50, ROOM, "off"))
    assert rig.person.last_detection == t(50)


def test_force_sets_state_and_detection():
    rig = out()
    trs = rig.person.force(t(10), Presence.IN_ROOM, "bedroom", "bed", "manual")
    assert rig.view == "bedroom/bed"
    assert rig.person.last_detection == t(10)
    assert [(tr.old, tr.new, tr.reason) for tr in trs] == [("out", "bedroom/bed", "manual")]


def test_force_drops_pending_exit():
    rig = in_room()
    rig.run((50, DOOR, "on"), (70, ROOM, "off"))
    rig.person.force(t(80), Presence.IN_ROOM, "bedroom", None, "manual")
    rig.tick(500)
    assert rig.person.presence is Presence.IN_ROOM
