"""Showering from a sharp humidity rise in a bathroom with recent presence."""

from datetime import UTC, datetime, timedelta

from custom_components.room_presence.engine.rooms import RoomSensors
from custom_components.room_presence.engine.shower import ShowerTracker
from custom_components.room_presence.engine.types import StateEvent, Timings
from tests.homes import HUM, THP, bathroom

BATH = THP.format(2)
DAY = datetime(2000, 2, 15, tzinfo=UTC)


def hm(clock: str) -> datetime:
    parts = [int(x) for x in clock.split(":")]
    h, m, s = (parts + [0])[:3]
    return DAY + timedelta(hours=h, minutes=m, seconds=s)


class Bath:
    def __init__(self) -> None:
        room = bathroom()
        self.sensors = RoomSensors(room, Timings())
        self.shower = ShowerTracker(room, Timings())
        self.log = []

    def feed(self, clock: str, entity: str, state: str):
        ev = StateEvent(hm(clock), entity, state)
        self.sensors.feed(ev)
        out = self.shower.feed(ev, self.sensors)
        self.log += out
        return out

    def humidity(self, *readings: tuple[str, float]):
        out = []
        for clock, value in readings:
            out += self.feed(clock, HUM, str(value))
        return out


def showering_from_22_06() -> Bath:
    b = Bath()
    b.feed("21:50", BATH, "off")
    b.humidity(("21:58", 37), ("22:03", 37))
    b.feed("22:06:13", BATH, "on")
    b.feed("22:06:41", BATH, "off")
    assert b.humidity(("22:09", 39)) == []
    trs = b.humidity(("22:11", 45))
    assert [(tr.at, tr.old, tr.new, tr.reason) for tr in trs] == [
        (hm("22:06:13"), "dry", "showering", "shower_start")
    ]
    return b


def test_rise_with_presence_starts():
    b = showering_from_22_06()
    assert b.shower.showering
    assert b.shower.since == hm("22:06:13")


def test_rise_without_presence_ignored():
    b = Bath()
    b.feed("21:00", BATH, "off")
    b.humidity(("22:03", 37), ("22:09", 39), ("22:11", 45), ("22:14", 49))
    assert not b.shower.showering
    assert b.log == []


def test_slow_drift_ignored():
    b = Bath()
    b.feed("21:00", BATH, "off")
    b.feed("21:01", BATH, "on")
    readings = [(f"{21 + (i * 5) // 60}:{(i * 5) % 60:02d}", 37 + 0.7 * i) for i in range(13)]
    b.humidity(*readings)  # +0.7 every 5 min: about +2 in any 15 min, +8.4 over the hour
    assert not b.shower.showering


def test_ends_after_no_new_high():
    b = showering_from_22_06()
    b.humidity(("22:14", 49), ("22:17", 53))
    assert b.shower.showering
    assert b.shower.next_deadline() == hm("22:20")
    trs = b.humidity(("22:20", 53))  # not a new high: the shower ends at its deadline
    assert [(tr.at, tr.old, tr.new, tr.reason) for tr in trs] == [
        (hm("22:20"), "showering", "dry", "shower_end")
    ]
    assert not b.shower.showering


def test_new_high_exactly_at_deadline_extends():
    b = showering_from_22_06()  # high 45 at 22:11
    assert b.humidity(("22:14", 49)) == []
    assert b.shower.showering
    assert b.shower.next_deadline() == hm("22:17")


def test_tick_at_deadline_ends():
    b = showering_from_22_06()
    trs = b.shower.tick(hm("22:14"))
    assert [(tr.at, tr.reason) for tr in trs] == [(hm("22:14"), "shower_end")]


def test_non_numeric_reading_ignored_mid_shower():
    b = showering_from_22_06()
    b.humidity(("22:14", 49), ("22:17", 53))
    assert b.feed("22:18", HUM, "unavailable") == []
    assert b.feed("22:19", HUM, "") == []
    assert b.shower.showering
    assert b.shower.next_deadline() == hm("22:20")


def test_end_explicit():
    b = showering_from_22_06()
    trs = b.shower.end(hm("22:12"), "exit")
    assert [(tr.new, tr.reason) for tr in trs] == [("dry", "exit")]
    assert not b.shower.showering
    assert b.shower.next_deadline() is None
    assert b.shower.end(hm("22:13"), "exit") == []


def test_threshold_option():
    room = bathroom()
    sensors = RoomSensors(room, Timings())
    shower = ShowerTracker(room, Timings(shower_rise=10.0))
    for clock, entity, state in (("22:00", BATH, "on"), ("22:01", HUM, "37"), ("22:05", HUM, "45")):
        ev = StateEvent(hm(clock), entity, state)
        sensors.feed(ev)
        shower.feed(ev, sensors)
    assert not shower.showering


def test_no_new_shower_from_the_tail_of_the_last():
    b = showering_from_22_06()
    b.humidity(("22:14", 49), ("22:17", 53), ("22:20", 53), ("22:24", 52), ("22:25", 51))
    starts = [tr for tr in b.log if tr.new == "showering"]
    assert len(starts) == 1
    assert not b.shower.showering
