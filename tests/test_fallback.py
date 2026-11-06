"""FP2 unavailable: degraded, hold, and the THP door fallback for exits and entries."""

from datetime import UTC, datetime, timedelta

from custom_components.room_presence.engine.room import RoomLayer
from custom_components.room_presence.engine.types import EngineConfig, RoomState, StateEvent, Sub

T0 = datetime(2000, 1, 10, 13, 0, tzinfo=UTC)
ROOM = "binary_sensor.fp2_room"
BED = "binary_sensor.fp2_bed"
DOOR = "binary_sensor.fp2_door"
THP_DOOR = "binary_sensor.thp_door"
THP_ROOM = "binary_sensor.thp_room"
THP_DESK = "binary_sensor.thp_desk"
BATH = "binary_sensor.thp_bath"

CFG = EngineConfig(
    fp2_room=ROOM,
    fp2_bed=BED,
    door_witnesses=(DOOR, THP_DOOR),
    bathroom=BATH,
    fallback_motion=(THP_ROOM, THP_DESK),
)


def t(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def run(layer: RoomLayer, *events: tuple[float, str, str]):
    out = []
    for s, e, st in events:
        out += layer.feed(StateEvent(t(s), e, st))
    return out


def degraded_in_bed(cfg=CFG) -> RoomLayer:
    layer = RoomLayer(cfg)
    run(
        layer,
        *[(0, e, "off") for e in (THP_DOOR, THP_ROOM, THP_DESK, BATH)],
        (0, ROOM, "on"),
        (1, BED, "on"),
        (100, BED, "unavailable"),
        (100, DOOR, "unavailable"),
        (100, ROOM, "unavailable"),
    )
    assert layer.degraded
    return layer


def test_degraded_before_fp2_reports():
    assert RoomLayer(CFG).degraded is True


def test_fp2_unavailable_sets_degraded_and_holds():
    layer = degraded_in_bed()
    assert layer.state is RoomState.IN_ROOM
    assert layer.sub is Sub.BED
    assert layer.reason == "fp2_unavailable"
    layer.tick(t(10_000))
    assert layer.state is RoomState.IN_ROOM


def door_exit(layer: RoomLayer, door_on: float = 10, quiet_from: float | None = None):
    """Motion at 200, door 210..210+door_on; returns transitions up to the end of the quiet."""
    out = run(
        layer,
        (200, THP_ROOM, "on"),
        (203, THP_ROOM, "off"),
        (210, THP_DOOR, "on"),
        (210 + door_on, THP_DOOR, "off"),
    )
    if quiet_from is not None:
        out += run(layer, (quiet_from, THP_DESK, "on"), (quiet_from + 4, THP_DESK, "off"))
    return out + layer.tick(t(210 + door_on + 181))


def test_fallback_exit_backdated_to_door_edge():
    layer = degraded_in_bed()
    trs = door_exit(layer)
    assert layer.state is RoomState.RECENTLY_LEFT
    assert [(tr.at, tr.old, tr.new, tr.reason) for tr in trs] == [
        (t(210), "bed", "recently_left", "fallback_exit")
    ]


def test_short_door_trigger_is_not_exit():
    layer = degraded_in_bed()
    door_exit(layer, door_on=7)
    assert layer.state is RoomState.IN_ROOM


def test_door_without_prior_motion_is_not_exit():
    layer = degraded_in_bed()
    run(layer, (210, THP_DOOR, "on"), (220, THP_DOOR, "off"))
    layer.tick(t(1000))
    assert layer.state is RoomState.IN_ROOM


def test_motion_after_door_cancels_fallback_exit():
    layer = degraded_in_bed()
    door_exit(layer, quiet_from=300)
    assert layer.state is RoomState.IN_ROOM


def test_motion_during_door_on_time_does_not_cancel():
    layer = degraded_in_bed()
    run(layer, (200, THP_ROOM, "on"), (203, THP_ROOM, "off"), (210, THP_DOOR, "on"))
    run(layer, (212, THP_DESK, "on"), (215, THP_DESK, "off"), (220, THP_DOOR, "off"))
    layer.tick(t(401))
    assert layer.state is RoomState.RECENTLY_LEFT


def gone(layer: RoomLayer) -> RoomLayer:
    door_exit(layer)
    layer.tick(t(1000))
    assert layer.state is RoomState.OUT
    return layer


def test_fallback_entry_needs_two_sensors():
    layer = gone(degraded_in_bed())
    run(layer, (2000, THP_DOOR, "on"), (2004, THP_DOOR, "off"), (2030, THP_ROOM, "on"))
    assert layer.state is RoomState.OUT
    trs = run(layer, (2033, THP_ROOM, "off"), (2060, THP_DESK, "on"))
    assert layer.state is RoomState.IN_ROOM
    assert layer.sub is Sub.ROOM
    assert [(tr.new, tr.reason) for tr in trs] == [("room", "fallback_entry")]


def test_fallback_entry_outside_window_is_ignored():
    layer = gone(degraded_in_bed())
    run(layer, (2000, THP_DOOR, "on"), (2004, THP_DOOR, "off"), (2030, THP_ROOM, "on"))
    run(layer, (2033, THP_ROOM, "off"), (2100, THP_DESK, "on"))
    assert layer.state is RoomState.OUT


def test_fallback_entry_with_one_available_sensor():
    cfg = EngineConfig(
        fp2_room=ROOM, fp2_bed=BED, door_witnesses=(THP_DOOR,), fallback_motion=(THP_ROOM, THP_DESK)
    )
    layer = degraded_in_bed(cfg)
    run(layer, (150, THP_DESK, "unavailable"))
    gone(layer)
    run(layer, (2000, THP_DOOR, "on"), (2004, THP_DOOR, "off"), (2030, THP_ROOM, "on"))
    assert layer.state is RoomState.IN_ROOM


def test_fp2_flap_restores_sub_location():
    layer = RoomLayer(CFG)
    run(layer, (0, ROOM, "on"), (1, BED, "on"))
    trs = run(
        layer,
        (100, ROOM, "unavailable"),
        (100, BED, "unavailable"),
        (105, ROOM, "on"),
        (105, BED, "on"),
    )
    assert layer.degraded is False
    assert layer.sub is Sub.BED
    trs += layer.tick(t(200))
    assert trs == []


def test_fp2_back_with_all_zones_off_keeps_holding():
    layer = degraded_in_bed()
    trs = run(layer, (500, ROOM, "off"), (500, BED, "off"))
    assert not layer.degraded
    assert layer.state is RoomState.IN_ROOM
    assert layer.sub is Sub.BED
    assert layer.reason == "fp2_back"
    assert trs == []


def test_no_door_witness_available_holds_while_degraded():
    cfg = EngineConfig(
        fp2_room=ROOM, fp2_bed=BED, door_witnesses=(DOOR,), fallback_motion=(THP_ROOM,)
    )
    layer = degraded_in_bed(cfg)
    run(layer, (200, THP_ROOM, "on"), (203, THP_ROOM, "off"))
    layer.tick(t(10_000))
    assert layer.state is RoomState.IN_ROOM
    assert layer.degraded


def test_zone_going_unavailable_while_held_does_not_recheck_exit():
    layer = RoomLayer(CFG)
    run(layer, (0, ROOM, "on"), (1, BED, "on"), (100, BED, "off"), (100, ROOM, "off"))
    assert layer.held
    trs = run(layer, (130, THP_DOOR, "on"), (135, THP_DOOR, "off"), (140, BED, "unavailable"))
    trs += layer.tick(t(1000))
    assert layer.state is RoomState.IN_ROOM
    assert trs == []
