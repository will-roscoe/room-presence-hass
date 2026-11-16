"""Moves between rooms: the move window, sensor bleed, held rooms, exclusive rooms and pinning."""

from custom_components.room_presence.engine.types import Presence
from tests.homes import FP2, THP, bathroom, bedroom, home
from tests.rig import Rig, t

ROOM, BED, DESK, DOOR = (FP2.format(z) for z in ("room", "bed", "desk", "door"))
BATH = THP.format(2)

PLAIN = home(bedroom(), bathroom(exclusive=False, humidity=None))
EXCL = home(bedroom(), bathroom(humidity=None))


def in_bedroom(h=PLAIN) -> Rig:
    rig = Rig(h)
    rig.run((0, BATH, "off"), (0, ROOM, "on"))
    assert rig.view == "bedroom"
    return rig


def test_bathroom_before_room_empties():
    rig = in_bedroom()
    rig.run((11, BATH, "on"))
    assert rig.view == "bedroom"  # bleed: ignored while the bedroom still sees someone
    trs = rig.run((23, ROOM, "off"))
    assert [(tr.at, tr.old, tr.new, tr.reason) for tr in trs] == [
        (t(23), "bedroom", "bathroom", "moved")
    ]


def test_bathroom_edge_within_move_window_after_empty():
    rig = in_bedroom()
    rig.run((90, DOOR, "on"), (92, DOOR, "off"), (100, ROOM, "off"))
    trs = rig.run((120, BATH, "on"))
    assert [(tr.at, tr.new, tr.reason) for tr in trs] == [(t(120), "bathroom", "moved")]
    rig.tick(500)
    assert rig.view == "bathroom"
    assert not [tr for tr in rig.log if tr.new == "just_left"]


def test_other_room_beats_exit_crossing():
    rig = in_bedroom()
    rig.run((80, DOOR, "on"), (82, DOOR, "off"), (95, BATH, "on"))
    trs = rig.run((100, ROOM, "off"))
    assert [(tr.new, tr.reason) for tr in trs] == [("bathroom", "moved")]
    rig.tick(500)
    assert rig.view == "bathroom"


def test_interior_room_cannot_be_entered_from_out():
    rig = Rig(PLAIN)
    rig.run((0, ROOM, "off"), (0, BATH, "off"))
    assert rig.person.presence is Presence.OUT
    rig.run((10, BATH, "on"))
    assert rig.person.presence is Presence.OUT
    trs = rig.run((20, ROOM, "on"))
    assert [(tr.new, tr.reason) for tr in trs] == [("bedroom", "entry")]


def test_any_room_enterable_when_no_room_has_exits():
    rig = Rig(home(bedroom(exits=()), bathroom(exclusive=False, humidity=None)))
    rig.run((0, ROOM, "off"), (0, BATH, "off"))
    trs = rig.run((10, BATH, "on"))
    assert [(tr.new, tr.reason) for tr in trs] == [("bathroom", "entry")]


def test_bleed_ignored_for_non_exclusive_room():
    rig = in_bedroom()
    assert rig.run((10, BATH, "on")) == []
    assert rig.view == "bedroom"


def test_held_room_released_by_other_room():
    rig = in_bedroom()
    rig.run((50, ROOM, "off"))
    assert rig.person.held
    trs = rig.run((60, BATH, "on"))
    assert [(tr.at, tr.new, tr.reason) for tr in trs] == [(t(60), "bathroom", "moved")]


def test_exclusive_overrides_occupied_room():
    rig = in_bedroom(EXCL)
    trs = rig.run((10, BATH, "on"))
    assert [(tr.at, tr.new, tr.reason) for tr in trs] == [(t(10), "bathroom", "exclusive")]
    assert rig.person.came_from == "bedroom"


def test_back_from_exclusive_on_spot_edge():
    rig = in_bedroom(EXCL)
    rig.run((10, BATH, "on"))
    trs = rig.run((30, BED, "on"))
    assert [(tr.new, tr.reason) for tr in trs] == [("bedroom/bed", "moved")]


def test_back_from_exclusive_after_quiet():
    rig = in_bedroom(EXCL)
    rig.run((10, BATH, "on"), (40, BATH, "off"))
    assert rig.view == "bathroom"
    assert rig.tick(159) == []
    trs = rig.tick(160)
    assert [(tr.at, tr.new, tr.reason) for tr in trs] == [(t(160), "bedroom", "exclusive_quiet")]


def test_stale_edges_from_before_entering_do_not_pull_back():
    # Pacing by the doors makes bed/desk edges just before the bathroom; a pause of the bathroom
    # sensor a few seconds later must not count them as the person coming back (13 Nov test).
    rig = in_bedroom(EXCL)
    rig.run((40, DESK, "on"), (41, DESK, "off"), (42, BED, "on"), (43, BED, "off"))
    rig.run((60, BATH, "on"))
    assert rig.view == "bathroom"
    assert rig.run((69, BATH, "off")) == []
    assert rig.view == "bathroom"


def test_quiet_restarts_when_the_exclusive_room_sees_someone_again():
    rig = in_bedroom(EXCL)
    rig.run((10, BATH, "on"), (40, BATH, "off"), (100, BATH, "on"), (105, BATH, "off"))
    rig.tick(200)
    assert rig.view == "bathroom"
    rig.tick(225)
    assert rig.view == "bedroom"


def test_pin_moves_and_holds():
    rig = in_bedroom(EXCL)
    trs = rig.person.pin(t(10), "bathroom")
    assert [(tr.new, tr.reason) for tr in trs] == [("bathroom", "shower")]
    assert rig.person.pinned == "bathroom"
    assert rig.run((20, BED, "on"), (25, BED, "off"), (26, ROOM, "off"), (27, ROOM, "on")) == []
    rig.tick(1000)
    assert rig.view == "bathroom"
    rig.person.unpin()
    trs = rig.run((1100, BED, "on"))
    assert [(tr.new, tr.reason) for tr in trs] == [("bedroom/bed", "moved")]
