"""A room's presence sensors unavailable: degraded, hold, and the fallback exit and entry rules."""

from custom_components.room_presence.engine.types import Presence
from tests.homes import FP2, LIVE, THP, bathroom, bedroom, home
from tests.rig import Rig, t

ROOM, BED, DOOR = FP2.format("room"), FP2.format("bed"), FP2.format("door")
THP_ROOM, BATH, THP_DOOR, THP_DESK = (THP.format(i) for i in (1, 2, 3, 4))

HOME = home(bedroom(desk=False, motion=(THP_ROOM, THP_DESK)), bathroom(humidity=None))


def degraded_in_bed(h=HOME) -> Rig:
    rig = Rig(h)
    rig.run(
        *[(0, e, "off") for e in (THP_DOOR, THP_ROOM, THP_DESK, BATH)],
        (0, ROOM, "on"),
        (1, BED, "on"),
        (100, BED, "unavailable"),
        (100, DOOR, "unavailable"),
        (100, ROOM, "unavailable"),
    )
    assert rig.rooms["bedroom"].degraded
    return rig


def test_fallback_sets_match_v01():
    motion, others = Rig(LIVE).person.fallback_sets("bedroom")
    assert motion == {THP_ROOM, THP_DOOR, THP_DESK, BATH}
    assert others == {THP_ROOM, THP_DESK, BATH}


def test_degraded_before_presence_reports():
    assert Rig(HOME).rooms["bedroom"].degraded is True


def test_unavailable_sets_degraded_and_holds():
    rig = degraded_in_bed()
    assert rig.view == "bedroom/bed"
    assert rig.person.reason == "unavailable"
    rig.tick(10_000)
    assert rig.person.presence is Presence.IN_ROOM


def door_exit(rig: Rig, door_on: float = 10, quiet_from: float | None = None):
    """Motion at 200, door 210..210+door_on; returns transitions up to the end of the quiet."""
    out = rig.run(
        (200, THP_ROOM, "on"),
        (203, THP_ROOM, "off"),
        (210, THP_DOOR, "on"),
        (210 + door_on, THP_DOOR, "off"),
    )
    if quiet_from is not None:
        out += rig.run((quiet_from, THP_DESK, "on"), (quiet_from + 4, THP_DESK, "off"))
    return out + rig.tick(210 + door_on + 181)


def test_fallback_exit_backdated_to_door_edge():
    rig = degraded_in_bed()
    trs = door_exit(rig)
    assert rig.person.presence is Presence.JUST_LEFT
    assert [(tr.at, tr.old, tr.new, tr.reason) for tr in trs] == [
        (t(210), "bedroom/bed", "just_left", "fallback_exit")
    ]


def test_short_door_trigger_is_not_exit():
    rig = degraded_in_bed()
    door_exit(rig, door_on=7)
    assert rig.person.presence is Presence.IN_ROOM


def test_door_without_prior_motion_is_not_exit():
    rig = degraded_in_bed()
    rig.run((210, THP_DOOR, "on"), (220, THP_DOOR, "off"))
    rig.tick(1000)
    assert rig.person.presence is Presence.IN_ROOM


def test_motion_after_door_cancels_fallback_exit():
    rig = degraded_in_bed()
    door_exit(rig, quiet_from=300)
    assert rig.person.presence is Presence.IN_ROOM


def test_motion_during_door_on_time_does_not_cancel():
    rig = degraded_in_bed()
    rig.run((200, THP_ROOM, "on"), (203, THP_ROOM, "off"), (210, THP_DOOR, "on"))
    rig.run((212, THP_DESK, "on"), (215, THP_DESK, "off"), (220, THP_DOOR, "off"))
    rig.tick(401)
    assert rig.person.presence is Presence.JUST_LEFT


def gone(rig: Rig) -> Rig:
    door_exit(rig)
    rig.tick(1000)
    assert rig.person.presence is Presence.OUT
    return rig


def test_fallback_entry_needs_two_sensors():
    rig = gone(degraded_in_bed())
    rig.run((2000, THP_DOOR, "on"), (2004, THP_DOOR, "off"), (2030, THP_ROOM, "on"))
    assert rig.person.presence is Presence.OUT
    trs = rig.run((2033, THP_ROOM, "off"), (2060, THP_DESK, "on"))
    assert rig.view == "bedroom"
    assert [(tr.new, tr.reason) for tr in trs] == [("bedroom", "fallback_entry")]


def test_fallback_entry_outside_window_is_ignored():
    rig = gone(degraded_in_bed())
    rig.run((2000, THP_DOOR, "on"), (2004, THP_DOOR, "off"), (2030, THP_ROOM, "on"))
    rig.run((2033, THP_ROOM, "off"), (2100, THP_DESK, "on"))
    assert rig.person.presence is Presence.OUT


def test_fallback_entry_with_one_available_sensor():
    h = home(bedroom(desk=False, exits=(THP_DOOR,), motion=(THP_ROOM, THP_DESK)))
    rig = degraded_in_bed(h)
    rig.run((150, THP_DESK, "unavailable"))
    gone(rig)
    rig.run((2000, THP_DOOR, "on"), (2004, THP_DOOR, "off"), (2030, THP_ROOM, "on"))
    assert rig.person.presence is Presence.IN_ROOM


def test_presence_flap_restores_spot():
    rig = Rig(HOME)
    rig.run((0, ROOM, "on"), (1, BED, "on"))
    trs = rig.run(
        (100, ROOM, "unavailable"),
        (100, BED, "unavailable"),
        (105, ROOM, "on"),
        (105, BED, "on"),
    )
    assert not rig.rooms["bedroom"].degraded
    assert rig.view == "bedroom/bed"
    trs += rig.tick(200)
    assert trs == []


def test_back_with_all_sensors_off_keeps_holding():
    rig = degraded_in_bed()
    trs = rig.run((500, ROOM, "off"), (500, BED, "off"))
    assert not rig.rooms["bedroom"].degraded
    assert rig.view == "bedroom/bed"
    assert rig.person.reason == "back"
    assert trs == []


def test_no_exit_sensor_available_holds_while_degraded():
    h = home(bedroom(desk=False, exits=(DOOR,), motion=(THP_ROOM,)))
    rig = degraded_in_bed(h)
    rig.run((200, THP_ROOM, "on"), (203, THP_ROOM, "off"))
    rig.tick(10_000)
    assert rig.person.presence is Presence.IN_ROOM
    assert rig.rooms["bedroom"].degraded


def test_spot_going_unavailable_while_held_does_not_recheck_exit():
    rig = Rig(HOME)
    rig.run((0, ROOM, "on"), (1, BED, "on"), (100, BED, "off"), (100, ROOM, "off"))
    assert rig.person.held
    trs = rig.run((130, THP_DOOR, "on"), (135, THP_DOOR, "off"), (140, BED, "unavailable"))
    trs += rig.tick(1000)
    assert rig.person.presence is Presence.IN_ROOM
    assert trs == []


def test_exit_during_short_outage_with_fallback_candidate():
    rig = Rig(HOME)
    rig.run(*[(0, e, "off") for e in (THP_DOOR, THP_ROOM, THP_DESK, BATH)], (0, ROOM, "on"))
    rig.run(
        (100, ROOM, "unavailable"),
        (100, DOOR, "unavailable"),
        (105, THP_ROOM, "on"),
        (109, THP_ROOM, "off"),
        (110, THP_DOOR, "on"),
        (122, THP_DOOR, "off"),
        (140, DOOR, "off"),
        (140, ROOM, "off"),
    )
    assert rig.person.presence is Presence.IN_ROOM  # still waiting for the fallback quiet period
    trs = rig.tick(122 + 180)
    assert rig.person.presence is Presence.JUST_LEFT
    assert [(tr.at, tr.reason) for tr in trs] == [(t(110), "fallback_exit")]


def test_back_empty_after_recent_door_edge_is_a_crossing():
    rig = Rig(HOME)
    rig.run(*[(0, e, "off") for e in (THP_DOOR, THP_ROOM, THP_DESK, BATH)], (0, ROOM, "on"))
    rig.run(
        (100, ROOM, "unavailable"),
        (110, THP_DOOR, "on"),
        (114, THP_DOOR, "off"),  # too short for the fallback rule
        (130, ROOM, "off"),
    )
    rig.tick(130 + 30)
    assert rig.person.presence is Presence.JUST_LEFT
    assert rig.person.since == t(110)


def test_back_with_a_sensor_on_cancels_fallback_exit():
    rig = Rig(HOME)
    rig.run(*[(0, e, "off") for e in (THP_DOOR, THP_ROOM, THP_DESK, BATH)], (0, ROOM, "on"))
    rig.run(
        (100, ROOM, "unavailable"),
        (105, THP_ROOM, "on"),
        (109, THP_ROOM, "off"),
        (110, THP_DOOR, "on"),
        (122, THP_DOOR, "off"),
        (140, ROOM, "on"),
    )
    rig.tick(1000)
    assert rig.person.presence is Presence.IN_ROOM
