"""Sleeping: in bed and still, or the sleep entities all off; wake rules."""

from datetime import UTC, datetime, timedelta

from custom_components.room_presence.engine.room import RoomLayer
from custom_components.room_presence.engine.sleep import SleepTracker
from custom_components.room_presence.engine.types import EngineConfig, StateEvent

T0 = datetime(2000, 1, 3, 22, 0, tzinfo=UTC)
ROOM = "binary_sensor.fp2_room"
BED = "binary_sensor.fp2_bed"
DOOR = "binary_sensor.fp2_door"
MOVE = "binary_sensor.thp_room"
L1 = "light.desk"
L2 = "light.ceiling"

CFG = EngineConfig(fp2_room=ROOM, fp2_bed=BED, door_witnesses=(DOOR,), movement=MOVE)
CFG_ENT = EngineConfig(fp2_room=ROOM, fp2_bed=BED, door_witnesses=(DOOR,), sleep_entities=(L1, L2))


def t(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


class Rig:
    """A room layer and a sleep tracker driven together, as the machine drives them."""

    def __init__(self, cfg: EngineConfig) -> None:
        self.room = RoomLayer(cfg)
        self.sleep = SleepTracker(cfg)
        self.log = []

    def tick(self, seconds: float) -> None:
        now = t(seconds)
        while True:
            ds = [d for d in (self.room.next_deadline(), self.sleep.next_deadline(self.room)) if d]
            if not ds or min(ds) > now:
                break
            self.room.tick(min(ds))
            self.log += self.sleep.tick(min(ds), self.room)
        self.log += self.sleep.tick(now, self.room)

    def feed(self, seconds: float, entity: str, state: str) -> None:
        self.tick(seconds)
        e = StateEvent(t(seconds), entity, state)
        self.room.feed(e)
        self.log += self.sleep.feed(e, self.room)


def in_bed(cfg=CFG) -> Rig:
    rig = Rig(cfg)
    for e in (MOVE, L1, L2, DOOR):
        rig.feed(0, e, "off")
    if cfg.sleep_entities:
        rig.feed(0, L1, "on")
        rig.feed(0, L2, "on")
    rig.feed(0, ROOM, "on")
    rig.feed(1, BED, "on")
    rig.feed(60, MOVE, "on")
    rig.feed(64, MOVE, "off")
    return rig


def asleep() -> Rig:
    rig = in_bed()
    rig.tick(1860)
    assert rig.sleep.sleeping
    return rig


def test_still_in_bed_for_30_min_sleeps_backdated():
    rig = in_bed()
    rig.tick(1859)
    assert not rig.sleep.sleeping
    assert rig.sleep.next_deadline(rig.room) == t(1860)
    rig.tick(1860)
    assert rig.sleep.sleeping
    assert rig.sleep.since == t(60)
    assert [(tr.at, tr.old, tr.new, tr.reason) for tr in rig.log] == [
        (t(60), "bed", "sleeping", "sleep_still")
    ]


def test_movement_resets_quiet_clock():
    rig = in_bed()
    rig.feed(1000, MOVE, "on")
    rig.feed(1004, MOVE, "off")
    rig.tick(1860)
    assert not rig.sleep.sleeping
    rig.tick(2800)
    assert rig.sleep.sleeping
    assert rig.sleep.since == t(1000)


def test_short_movement_does_not_wake():
    rig = asleep()
    rig.feed(3000, MOVE, "on")
    rig.feed(3007, MOVE, "off")
    rig.tick(3100)
    assert rig.sleep.sleeping


def test_long_movement_wakes():
    rig = asleep()
    rig.feed(3000, MOVE, "on")
    rig.feed(3009, MOVE, "off")
    assert not rig.sleep.sleeping
    assert [(tr.at, tr.new, tr.reason) for tr in rig.log[-1:]] == [
        (t(3008), "bed", "wake_movement")
    ]


def test_entities_all_off_sleeps_at_once():
    rig = in_bed(CFG_ENT)
    rig.feed(100, L1, "off")
    assert not rig.sleep.sleeping
    rig.feed(200, L2, "off")
    assert rig.sleep.sleeping
    assert rig.sleep.since == t(200)
    assert rig.log[-1].reason == "sleep_entities"


def test_entities_off_before_bed_sleep_once_in_bed():
    rig = Rig(CFG_ENT)
    rig.feed(0, L1, "on")
    rig.feed(0, L2, "on")
    rig.feed(0, ROOM, "on")
    rig.feed(10, L1, "off")
    rig.feed(11, L2, "off")
    assert not rig.sleep.sleeping
    rig.feed(30, BED, "on")
    assert rig.sleep.sleeping
    assert rig.sleep.since == t(30)


def test_entity_route_does_not_reenter_after_wake():
    cfg = EngineConfig(
        fp2_room=ROOM, fp2_bed=BED, door_witnesses=(DOOR,), movement=MOVE, sleep_entities=(L1,)
    )
    rig = in_bed(cfg)
    rig.feed(100, L1, "off")
    assert rig.sleep.sleeping
    rig.feed(200, MOVE, "on")
    rig.feed(210, MOVE, "off")
    assert not rig.sleep.sleeping
    rig.tick(500)
    assert not rig.sleep.sleeping


def test_entity_turning_on_wakes_on_entity_route():
    rig = in_bed(CFG_ENT)
    rig.feed(100, L1, "off")
    rig.feed(101, L2, "off")
    rig.feed(500, L2, "on")
    assert not rig.sleep.sleeping
    assert rig.log[-1].reason == "wake_entities"


def test_bed_blip_under_grace_keeps_sleeping():
    rig = asleep()
    rig.feed(3000, BED, "off")
    rig.feed(3019, BED, "on")
    rig.tick(3100)
    assert rig.sleep.sleeping


def test_leaving_bed_over_grace_wakes():
    rig = asleep()
    rig.feed(3000, BED, "off")
    rig.tick(3015)
    assert rig.sleep.sleeping
    rig.tick(3020)
    assert not rig.sleep.sleeping
    assert [(tr.at, tr.reason) for tr in rig.log[-1:]] == [(t(3000), "wake_left_bed")]


def test_crossing_wakes():
    rig = asleep()
    rig.feed(3000, DOOR, "on")
    rig.feed(3001, BED, "off")
    rig.feed(3002, ROOM, "off")
    assert not rig.sleep.sleeping


def test_no_movement_sensor_and_no_entities_never_sleeps():
    rig = in_bed(EngineConfig(fp2_room=ROOM, fp2_bed=BED, door_witnesses=(DOOR,)))
    rig.tick(10 * 3600)
    assert not rig.sleep.sleeping
    assert rig.sleep.next_deadline(rig.room) is None


def test_unavailable_movement_sensor_disables_stillness():
    rig = in_bed()
    rig.feed(100, MOVE, "unavailable")
    rig.tick(4 * 3600)
    assert not rig.sleep.sleeping


def test_force_sleeping():
    rig = in_bed()
    rig.sleep.force(t(200), True)
    assert rig.sleep.sleeping
    assert rig.sleep.since == t(200)
