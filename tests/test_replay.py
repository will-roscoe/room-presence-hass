"""Replay recorded days through the engine and check the labelled exits, still gaps and sleeps.

Fixtures are real sensor history, time-shifted by whole days: ``fp2_day.json`` starts on
2000-01-03 (D0) and ``fallback_day.json`` on 2000-01-10. Times below are UTC.
"""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

from custom_components.room_presence.engine.machine import PresenceMachine
from custom_components.room_presence.engine.replay import history_events, replay
from custom_components.room_presence.engine.types import (
    EngineConfig,
    ManualEvent,
    StateEvent,
    Transition,
)

FIXTURES = Path(__file__).parent / "fixtures"
FP2 = "binary_sensor.aqara_fp2_{}_presence"
THP = "binary_sensor.tuya_thp_{}_presence"
IN_ROOM = {"bed", "desk", "room", "bathroom", "sleeping"}

FP2_CFG = EngineConfig(
    fp2_room=FP2.format("room"),
    fp2_bed=FP2.format("bed"),
    fp2_desk=FP2.format("desk"),
    door_witnesses=(FP2.format("door"), THP.format(3)),
    movement=THP.format(1),
)
FALLBACK_CFG = EngineConfig(
    fp2_room=FP2.format("room"),
    fp2_bed=FP2.format("bed"),
    fp2_desk=FP2.format("desk"),
    door_witnesses=(FP2.format("door"), THP.format(3)),
    bathroom=THP.format(2),
    movement=THP.format(1),
    fallback_motion=(THP.format(1), THP.format(4)),
)


def at(day: int, hms: str, base: datetime = datetime(2000, 1, 3, tzinfo=UTC)) -> datetime:
    h, m, *s = (int(x) for x in hms.split(":"))
    return base + timedelta(days=day, hours=h, minutes=m, seconds=s[0] if s else 0)


class Timeline:
    """Location over effective time, from a replay's transitions."""

    def __init__(self, transitions: list[Transition]) -> None:
        self.trs = sorted(transitions, key=lambda tr: tr.at)

    def at(self, when: datetime) -> str:
        loc = "unknown"
        for tr in self.trs:
            if tr.at > when:
                break
            loc = tr.new
        return loc

    def samples(self, start: datetime, end: datetime, step: float = 30):
        when = start
        while when <= end:
            yield when, self.at(when)
            when += timedelta(seconds=step)


@pytest.fixture(scope="module")
def fp2_day() -> Timeline:
    history = json.loads((FIXTURES / "fp2_day.json").read_text())
    return Timeline(replay(PresenceMachine(FP2_CFG, []), history_events(history)))


# -- the replay functions ----------------------------------------------------------------------


def test_history_events_sorted_and_parsed():
    t1 = datetime(2000, 1, 1, 0, 0, 1, tzinfo=UTC)
    history = {
        "b": [["2000-01-01T00:00:02+00:00", "on"], [t1, "off"]],
        "a": [["2000-01-01T00:00:01+00:00", "on"]],
    }
    events = history_events(history)
    assert [(e.entity_id, e.state) for e in events] == [("b", "off"), ("a", "on"), ("b", "on")]
    assert events[1].at == t1


def test_replay_ticks_deadlines_between_events():
    cfg = EngineConfig(fp2_room="r", door_witnesses=("d",))
    t0 = datetime(2000, 1, 1, tzinfo=UTC)
    events = [
        StateEvent(t0, "r", "on"),
        StateEvent(t0 + timedelta(seconds=40), "d", "on"),
        StateEvent(t0 + timedelta(seconds=70), "r", "off"),
        StateEvent(t0 + timedelta(hours=1), "d", "off"),
    ]
    trs = replay(PresenceMachine(cfg, []), events)
    assert [(tr.new, tr.reason) for tr in trs] == [
        ("room", "fp2_zone"),
        ("recently_left", "crossing"),
        ("away", "recently_left_expired"),
    ]
    assert trs[2].at == t0 + timedelta(seconds=340)


def test_replay_until_runs_trailing_deadlines():
    cfg = EngineConfig(fp2_room="r", door_witnesses=("d",))
    t0 = datetime(2000, 1, 1, tzinfo=UTC)
    events = [StateEvent(t0, "r", "on"), StateEvent(t0, "d", "off")]
    events += [StateEvent(t0 + timedelta(seconds=40), "d", "on")]
    events += [StateEvent(t0 + timedelta(seconds=70), "r", "off")]
    assert replay(PresenceMachine(cfg, []), events)[-1].new == "recently_left"
    trs = replay(PresenceMachine(cfg, []), events, until=t0 + timedelta(hours=1))
    assert trs[-1].new == "away"


# -- the labelled FP2 day (4-5 Nov) ------------------------------------------------------------

EXITS = {
    "A": (at(0, "18:49:05"), at(0, "18:56:05")),
    "B": (at(0, "18:56:37"), at(0, "18:57:24")),
    "C": (at(0, "19:18:17"), at(0, "19:20:11")),
    "D": (at(0, "23:23:01"), at(0, "23:27:54")),
    "E": (at(0, "23:53:28"), at(0, "23:55:42")),
    "H": (at(1, "11:28:45"), at(1, "11:31:08")),
    "K": (at(1, "15:13:24"), at(1, "15:14:09")),
    "L": (at(1, "16:17:37"), at(1, "16:17:43")),
    "M": (at(1, "16:19:09"), at(1, "16:21:27")),
}
STILL = {
    "F": (at(1, "02:05:25"), at(1, "02:38:20")),
    "G": (at(1, "03:38:21"), at(1, "03:46:24")),
    "I": (at(1, "12:57:21"), at(1, "13:06:58")),
    "J": (at(1, "13:39:48"), at(1, "13:48:07")),
}


@pytest.mark.parametrize("label", sorted(EXITS))
def test_fp2_day_exits(fp2_day, label):
    start, end = EXITS[label]
    left = [
        tr
        for tr in fp2_day.trs
        if tr.new == "recently_left" and start - timedelta(seconds=60) <= tr.at <= start
    ]
    assert left, f"exit {label}: no recently_left in the minute before {start:%H:%M:%S}"
    inside = [
        (w, loc)
        for w, loc in fp2_day.samples(start + timedelta(seconds=5), end - timedelta(seconds=5), 1)
        if loc in IN_ROOM
    ]
    assert not inside, f"exit {label}: in room during the gap: {inside[:3]}"
    # The return is an entry transition. Checked as a transition, not on the effective-time
    # timeline: a short return (A's lasted 32 s) is hidden there by the next exit back-dating
    # its recently_left to a door edge seen during the return.
    back = [
        tr for tr in fp2_day.trs if tr.new in IN_ROOM and end <= tr.at <= end + timedelta(seconds=5)
    ]
    assert back, f"exit {label}: no return within 5 s of {end:%H:%M:%S}"


@pytest.mark.parametrize("label", sorted(STILL))
def test_fp2_day_still_gaps_stay_in_bed(fp2_day, label):
    start, end = STILL[label]
    wrong = [(w, loc) for w, loc in fp2_day.samples(start, end) if loc not in ("bed", "sleeping")]
    assert not wrong, f"still gap {label}: {wrong[:3]}"


def test_fp2_day_nap(fp2_day):
    wrong = [
        (w, loc) for w, loc in fp2_day.samples(at(0, "20:08"), at(0, "20:52")) if loc != "sleeping"
    ]
    assert not wrong, wrong[:3]


def test_fp2_day_night_unbroken(fp2_day):
    wrong = [
        (w, loc) for w, loc in fp2_day.samples(at(1, "01:36"), at(1, "11:23")) if loc != "sleeping"
    ]
    assert not wrong, wrong[:3]
    starts = [
        tr.at
        for tr in fp2_day.trs
        if tr.new == "sleeping" and at(1, "00:30") <= tr.at <= at(1, "01:36")
    ]
    assert starts and at(1, "01:00") <= starts[-1] <= at(1, "01:35"), starts


def test_fp2_day_midday_sleep(fp2_day):
    # Labelled "asleep 12:29": the time the rule fires, 30 min after the last movement, which is
    # also where the night's 01:35 label sits. The transition itself is back-dated.
    quiet = FP2_CFG.sleep_quiet
    starts = [
        tr.at
        for tr in fp2_day.trs
        if tr.new == "sleeping" and at(1, "11:30") <= tr.at <= at(1, "13:30")
    ]
    assert any(at(1, "12:28") <= s + quiet <= at(1, "12:31") for s in starts), starts
    ends = [
        tr.at
        for tr in fp2_day.trs
        if tr.old == "sleeping" and at(1, "12:31") <= tr.at <= at(1, "14:30")
    ]
    assert ends and at(1, "13:48") <= ends[0] <= at(1, "13:51"), ends


def test_fp2_day_no_bathroom_without_sensor(fp2_day):
    assert not [tr for tr in fp2_day.trs if tr.new == "bathroom"]


# -- the fallback day (30-31 Oct, THP sensors only) --------------------------------------------


def test_fallback_day():
    base = datetime(2000, 1, 10, tzinfo=UTC)
    history = json.loads((FIXTURES / "fallback_day.json").read_text())
    events = [ManualEvent(at(0, "13:28:30", base), "room"), *history_events(history)]
    m = PresenceMachine(FALLBACK_CFG, [])
    trs = replay(m, events)
    assert m.snapshot().degraded
    left = [tr.at for tr in trs if tr.new == "recently_left"]
    assert len(left) == 2, left
    for got, want in zip(left, ("13:31:03", "15:30:32"), strict=True):
        assert abs(got - at(0, want, base)) <= timedelta(seconds=1), (got, want)
    late = [tr for tr in trs if tr.at >= at(0, "15:36", base) and tr.new in IN_ROOM]
    assert not late, late
