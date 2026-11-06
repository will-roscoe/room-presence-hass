"""FP2 room layer: sub-location, crossings, holding, bathroom, entry, recently_left."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.room_presence.engine.room import ZONE_SETTLE, RoomLayer
from custom_components.room_presence.engine.types import EngineConfig, RoomState, StateEvent, Sub

T0 = datetime(2000, 1, 3, 12, 0, tzinfo=UTC)
ROOM = "binary_sensor.fp2_room"
BED = "binary_sensor.fp2_bed"
DESK = "binary_sensor.fp2_desk"
DOOR = "binary_sensor.fp2_door"
THP_DOOR = "binary_sensor.thp_door"
BATH = "binary_sensor.thp_bath"

CFG = EngineConfig(fp2_room=ROOM, fp2_bed=BED, fp2_desk=DESK, door_witnesses=(DOOR, THP_DOOR))
CFG_BATH = EngineConfig(
    fp2_room=ROOM, fp2_bed=BED, fp2_desk=DESK, door_witnesses=(DOOR, THP_DOOR), bathroom=BATH
)


def t(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def ev(seconds: float, entity: str, state: str) -> StateEvent:
    return StateEvent(t(seconds), entity, state)


def run(layer: RoomLayer, *events: StateEvent):
    out = []
    for e in events:
        out += layer.feed(e)
    return out


def in_room(cfg=CFG) -> RoomLayer:
    layer = RoomLayer(cfg)
    run(layer, ev(0, ROOM, "on"))
    assert layer.state is RoomState.IN_ROOM
    return layer


def recently_left(cfg=CFG) -> RoomLayer:
    layer = in_room(cfg)
    run(layer, ev(40, THP_DOOR, "on"), ev(45, THP_DOOR, "off"), ev(70, ROOM, "off"))
    assert layer.state is RoomState.RECENTLY_LEFT
    return layer


def out() -> RoomLayer:
    layer = RoomLayer(CFG)
    run(layer, ev(0, ROOM, "off"))
    assert layer.state is RoomState.OUT
    return layer


def test_starts_unknown():
    assert RoomLayer(CFG).state is RoomState.UNKNOWN


def test_sub_location_prefers_bed_then_desk_then_room():
    layer = in_room()
    assert layer.sub is Sub.ROOM
    run(layer, ev(10, BED, "on"))
    assert layer.sub is Sub.BED
    run(layer, ev(20, DESK, "on"))
    assert layer.sub is Sub.BED
    run(layer, ev(30, BED, "off"))
    assert layer.sub is Sub.DESK
    run(layer, ev(40, DESK, "off"))
    assert layer.sub is Sub.DESK  # dropping to "room" waits for the zones to settle
    assert layer.next_deadline() == t(40) + ZONE_SETTLE
    trs = layer.tick(t(40) + ZONE_SETTLE)
    assert layer.sub is Sub.ROOM
    assert [(tr.at, tr.old, tr.new) for tr in trs] == [(t(40), "desk", "room")]


def test_empty_without_crossing_holds_last_zone():
    layer = in_room()
    trs = run(layer, ev(1, BED, "on"), ev(100, BED, "off"), ev(100, ROOM, "off"))
    assert layer.state is RoomState.IN_ROOM
    assert layer.sub is Sub.BED
    assert layer.held is True
    assert layer.detected is False
    assert not [tr for tr in trs if tr.reason == "crossing"]


def test_empty_after_crossing_is_recently_left_backdated():
    layer = in_room()
    trs = run(layer, ev(40, THP_DOOR, "on"), ev(45, THP_DOOR, "off"), ev(70, ROOM, "off"))
    assert layer.state is RoomState.RECENTLY_LEFT
    assert [(tr.at, tr.old, tr.new, tr.reason) for tr in trs] == [
        (t(40), "room", "recently_left", "crossing")
    ]
    assert layer.since == t(40)


def test_witness_edge_older_than_window_is_not_a_crossing():
    layer = in_room()
    run(layer, ev(9, DOOR, "on"), ev(12, DOOR, "off"), ev(70, ROOM, "off"))
    assert layer.state is RoomState.IN_ROOM
    assert layer.held is True


def test_bathroom_edge_within_window_gives_bathroom():
    layer = in_room(CFG_BATH)
    run(layer, ev(40, DOOR, "on"), ev(50, BATH, "on"), ev(60, ROOM, "off"))
    assert layer.state is RoomState.IN_ROOM
    assert layer.sub is Sub.BATHROOM
    layer = in_room(CFG_BATH)
    run(layer, ev(40, DOOR, "on"), ev(45, ROOM, "off"), ev(55, BATH, "on"))
    assert layer.sub is Sub.BATHROOM
    assert layer.reason == "bathroom"


def test_no_bathroom_edge_gives_recently_left_at_deadline():
    layer = in_room(CFG_BATH)
    run(layer, ev(40, DOOR, "on"), ev(60, ROOM, "off"))
    assert layer.state is RoomState.IN_ROOM
    assert layer.next_deadline() == t(70)
    trs = layer.tick(t(70))
    assert layer.state is RoomState.RECENTLY_LEFT
    assert [(tr.at, tr.new, tr.reason) for tr in trs] == [(t(40), "recently_left", "crossing")]


def test_bathroom_edge_after_window_is_too_late():
    layer = in_room(CFG_BATH)
    run(layer, ev(40, DOOR, "on"), ev(60, ROOM, "off"), ev(75, BATH, "on"))
    assert layer.state is RoomState.RECENTLY_LEFT


def test_bathroom_ends_on_any_fp2_zone():
    layer = in_room(CFG_BATH)
    run(layer, ev(40, DOOR, "on"), ev(50, BATH, "on"), ev(60, ROOM, "off"))
    assert layer.sub is Sub.BATHROOM
    run(layer, ev(600, BATH, "off"), ev(900, BATH, "on"))
    assert layer.sub is Sub.BATHROOM
    trs = run(layer, ev(1000, DESK, "on"))
    assert layer.sub is Sub.DESK
    assert [(tr.old, tr.new, tr.reason) for tr in trs] == [("bathroom", "desk", "fp2_zone")]


def test_door_zone_alone_is_not_entry():
    layer = out()
    run(layer, ev(10, DOOR, "on"), ev(20, DOOR, "off"))
    assert layer.state is RoomState.OUT


def test_room_zone_is_entry_and_short_trip():
    layer = recently_left()
    trs = run(layer, ev(120, ROOM, "on"))
    assert layer.state is RoomState.IN_ROOM
    assert layer.sub is Sub.ROOM
    assert [(tr.old, tr.new, tr.reason) for tr in trs] == [("recently_left", "room", "entry")]


def test_entry_needs_crossing_option():
    cfg = EngineConfig(fp2_room=ROOM, door_witnesses=(DOOR,), entry_needs_crossing=True)
    layer = RoomLayer(cfg)
    run(layer, ev(0, ROOM, "off"))
    run(layer, ev(10, ROOM, "on"))
    assert layer.state is RoomState.OUT
    run(layer, ev(20, ROOM, "off"), ev(100, DOOR, "on"), ev(130, ROOM, "on"))
    assert layer.state is RoomState.IN_ROOM


def test_recently_left_expires_to_out_after_5_min_from_backdated_start():
    layer = recently_left()
    assert layer.next_deadline() == t(340)
    assert layer.tick(t(339)) == []
    trs = layer.tick(t(340))
    assert layer.state is RoomState.OUT
    assert [(tr.at, tr.old, tr.new, tr.reason) for tr in trs] == [
        (t(340), "recently_left", "out", "recently_left_expired")
    ]


@pytest.mark.parametrize("first", [BED, ROOM])
def test_same_second_zone_changes(first):
    layer = in_room()
    run(layer, ev(5, BED, "on"))
    second = ROOM if first == BED else BED
    trs = run(layer, ev(100, first, "off"), ev(100, second, "off"))
    assert layer.state is RoomState.IN_ROOM
    assert layer.sub is Sub.BED
    assert layer.held is True
    assert trs == []


def test_zone_lag_of_a_few_seconds_still_holds_bed():
    layer = in_room()
    run(layer, ev(5, BED, "on"))
    trs = run(layer, ev(100, BED, "off"), ev(101, ROOM, "off"))
    assert layer.sub is Sub.BED
    assert trs == []


def test_unavailable_witness_is_ignored():
    layer = in_room()
    run(layer, ev(1, THP_DOOR, "unavailable"), ev(40, DOOR, "on"), ev(70, ROOM, "off"))
    assert layer.state is RoomState.RECENTLY_LEFT
    layer = in_room()
    run(
        layer,
        ev(1, THP_DOOR, "unavailable"),
        ev(1, DOOR, "unavailable"),
        ev(60, THP_DOOR, "on"),  # unavailable -> on is not an edge
        ev(70, ROOM, "off"),
    )
    assert layer.state is RoomState.IN_ROOM
    assert layer.held is True


def test_unknown_then_off_is_out():
    layer = RoomLayer(CFG)
    trs = run(layer, ev(0, ROOM, "off"))
    assert layer.state is RoomState.OUT
    assert [(tr.old, tr.new) for tr in trs] == [("unknown", "out")]


def test_last_detection_tracks_fp2():
    layer = in_room()
    run(layer, ev(5, BED, "on"), ev(50, BED, "off"), ev(50, ROOM, "off"))
    assert layer.last_detection == t(50)


def test_force_sets_state_and_detection():
    layer = out()
    trs = layer.force(t(10), RoomState.IN_ROOM, Sub.BED, "manual")
    assert layer.state is RoomState.IN_ROOM
    assert layer.sub is Sub.BED
    assert layer.last_detection == t(10)
    assert [(tr.old, tr.new, tr.reason) for tr in trs] == [("out", "bed", "manual")]
