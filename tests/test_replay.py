"""Replay recorded days through the home engine and check the labelled exits, sleeps and showers.

Fixtures are real sensor history, time-shifted. ``fp2_day.json`` starts on 2000-01-03 (D0) and
``fallback_day.json`` on 2000-01-10 (v0.1 shift). ``showers.json``, ``absence.json``,
``bathroom_test.json`` and ``evening.json`` are shifted from November 2026 to February 2000 with the
same day of month and clock time. Times below are UTC.
"""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

from custom_components.room_presence.engine import (
    HomeMachine,
    ManualEvent,
    StateEvent,
    Transition,
    history_events,
    replay,
)
from custom_components.room_presence.engine.rooms import RoomSensors
from custom_components.room_presence.engine.shower import ShowerTracker
from tests.homes import LIVE, THP, bathroom, bedroom, home

FIXTURES = Path(__file__).parent / "fixtures"
ROOM_LOCATIONS = {"bedroom", "bed", "desk", "bathroom"}

FP2_HOME = home(bedroom())
FALLBACK_HOME = home(
    bedroom(motion=(THP.format(1), THP.format(4))), bathroom(exclusive=False, humidity=None)
)


def at(day: int, hms: str, base: datetime = datetime(2000, 1, 3, tzinfo=UTC)) -> datetime:
    h, m, *s = (int(x) for x in hms.split(":"))
    return base + timedelta(days=day, hours=h, minutes=m, seconds=s[0] if s else 0)


def feb(day: int, hms: str) -> datetime:
    return at(day - 1, hms, datetime(2000, 2, 1, tzinfo=UTC))


def in_room(key: str) -> bool:
    return key.split(":")[0] in ROOM_LOCATIONS


def asleep(key: str) -> bool:
    return key.endswith(":asleep")


class Timeline:
    """Status key over effective time, from a replay's transitions."""

    def __init__(self, transitions: list[Transition]) -> None:
        self.trs = sorted(transitions, key=lambda tr: tr.at)

    def at(self, when: datetime) -> str:
        key = "unknown"
        for tr in self.trs:
            if tr.at > when:
                break
            key = tr.new
        return key

    def samples(self, start: datetime, end: datetime, step: float = 30):
        when = start
        while when <= end:
            yield when, self.at(when)
            when += timedelta(seconds=step)


def load(name: str) -> list[StateEvent]:
    return history_events(json.loads((FIXTURES / name).read_text()))


@pytest.fixture(scope="module")
def fp2_day() -> Timeline:
    return Timeline(replay(HomeMachine(FP2_HOME, []), load("fp2_day.json")))


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


def _exit_events(t0: datetime) -> list[StateEvent]:
    room, door = bedroom().presence[0], bedroom().exits[0]
    return [
        StateEvent(t0, room, "on"),
        StateEvent(t0, door, "off"),
        StateEvent(t0 + timedelta(seconds=40), door, "on"),
        StateEvent(t0 + timedelta(seconds=70), room, "off"),
        StateEvent(t0 + timedelta(hours=1), door, "off"),
    ]


def test_replay_ticks_deadlines_between_events():
    t0 = datetime(2000, 1, 1, tzinfo=UTC)
    trs = replay(HomeMachine(home(bedroom(), person=None), []), _exit_events(t0))
    assert [(tr.new, tr.reason) for tr in trs] == [
        ("bedroom", "detected"),
        ("just_left", "crossing"),
        ("away", "just_left_expired"),
    ]
    assert trs[2].at == t0 + timedelta(seconds=340)


def test_replay_until_runs_trailing_deadlines():
    t0 = datetime(2000, 1, 1, tzinfo=UTC)
    events = _exit_events(t0)[:-1]
    assert replay(HomeMachine(home(bedroom()), []), events)[-1].new == "bedroom"
    trs = replay(HomeMachine(home(bedroom()), []), events, until=t0 + timedelta(hours=1))
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
        if tr.new == "just_left" and start - timedelta(seconds=60) <= tr.at <= start
    ]
    assert left, f"exit {label}: no just_left in the minute before {start:%H:%M:%S}"
    inside = [
        (w, key)
        for w, key in fp2_day.samples(start + timedelta(seconds=5), end - timedelta(seconds=5), 1)
        if in_room(key)
    ]
    assert not inside, f"exit {label}: in a room during the gap: {inside[:3]}"
    # The return is an entry transition, checked as a transition: a short return (A's lasted
    # 32 s) is hidden on the effective-time timeline by the next exit back-dating its just_left.
    back = [
        tr for tr in fp2_day.trs if in_room(tr.new) and end <= tr.at <= end + timedelta(seconds=5)
    ]
    assert back, f"exit {label}: no return within 5 s of {end:%H:%M:%S}"


@pytest.mark.parametrize("label", sorted(STILL))
def test_fp2_day_still_gaps_stay_in_bed(fp2_day, label):
    start, end = STILL[label]
    wrong = [(w, k) for w, k in fp2_day.samples(start, end) if k not in ("bed", "bed:asleep")]
    assert not wrong, f"still gap {label}: {wrong[:3]}"


def test_fp2_day_nap(fp2_day):
    wrong = [(w, k) for w, k in fp2_day.samples(at(0, "20:08"), at(0, "20:52")) if not asleep(k)]
    assert not wrong, wrong[:3]


def test_fp2_day_night_unbroken(fp2_day):
    wrong = [(w, k) for w, k in fp2_day.samples(at(1, "01:36"), at(1, "11:23")) if not asleep(k)]
    assert not wrong, wrong[:3]
    starts = [
        tr.at for tr in fp2_day.trs if asleep(tr.new) and at(1, "00:30") <= tr.at <= at(1, "01:36")
    ]
    assert starts and at(1, "01:00") <= starts[-1] <= at(1, "01:35"), starts


def test_fp2_day_midday_sleep(fp2_day):
    # Labelled "asleep 12:29": the time the rule fires, 30 min after the last movement, which is
    # also where the night's 01:35 label sits. The transition itself is back-dated.
    quiet = FP2_HOME.timings.sleep_quiet
    starts = [
        tr.at for tr in fp2_day.trs if asleep(tr.new) and at(1, "11:30") <= tr.at <= at(1, "13:30")
    ]
    assert any(at(1, "12:28") <= s + quiet <= at(1, "12:31") for s in starts), starts
    ends = [
        tr.at
        for tr in fp2_day.trs
        if asleep(tr.old) and not asleep(tr.new) and at(1, "12:31") <= tr.at <= at(1, "14:30")
    ]
    assert ends and at(1, "13:48") <= ends[0] <= at(1, "13:51"), ends


def test_fp2_day_no_bathroom_without_bathroom_room(fp2_day):
    assert not [tr for tr in fp2_day.trs if tr.new.startswith("bathroom")]


# -- the fallback day (30-31 Oct, THP sensors only) --------------------------------------------


def test_fallback_day():
    base = datetime(2000, 1, 10, tzinfo=UTC)
    events = [ManualEvent(at(0, "13:28:30", base), location="bedroom"), *load("fallback_day.json")]
    m = HomeMachine(FALLBACK_HOME, [])
    trs = replay(m, events)
    assert "bedroom" in m.snapshot().degraded
    left = [tr.at for tr in trs if tr.new == "just_left"]
    assert len(left) == 2, left
    for got, want in zip(left, ("13:31:03", "15:30:32"), strict=True):
        assert abs(got - at(0, want, base)) <= timedelta(seconds=1), (got, want)
    late = [tr for tr in trs if tr.at >= at(0, "15:36", base) and in_room(tr.new)]
    assert not late, late


# -- November 2026 on the live configuration ---------------------------------------------------


def test_showers_exactly_three():
    room = LIVE.room("bathroom")
    sensors, shower = RoomSensors(room, LIVE.timings), ShowerTracker(room, LIVE.timings)
    starts = []
    for ev in load("showers.json"):
        shower.observe(ev)
        starts += shower.tick(ev.at)
        sensors.feed(ev)
        starts += shower.feed(ev, sensors)
    starts = [tr.at for tr in starts if tr.new == "showering"]
    windows = [
        (feb(9, "12:38"), feb(9, "12:58")),  # back-dated to the bathroom entry, 12:40:36
        (feb(11, "11:33"), feb(11, "11:45")),
        (feb(15, "22:05"), feb(15, "22:12")),
    ]
    assert len(starts) == 3, starts
    for got, (lo, hi) in zip(starts, windows, strict=True):
        assert lo <= got <= hi, (got, lo, hi)


def live_timeline(name: str) -> Timeline:
    return Timeline(replay(HomeMachine(LIVE, []), load(name)))


def test_absence_no_detection():
    tl = live_timeline("absence.json")
    left = [tr.at for tr in tl.trs if tr.new == "just_left"]
    assert any(abs(t - feb(11, "12:06:08")) <= timedelta(seconds=5) for t in left), left
    inside = [(w, k) for w, k in tl.samples(feb(11, "12:07"), feb(11, "18:51:00")) if in_room(k)]
    assert not inside, inside[:3]
    assert in_room(tl.at(feb(11, "18:51:15")))


def test_bathroom_test():
    tl = live_timeline("bathroom_test.json")
    pacing = list(tl.samples(feb(13, "12:34:00"), feb(13, "12:35:09"), 1))
    assert not [(w, k) for w, k in pacing if k in ("just_left", "bathroom")], pacing
    assert all(
        k == "bathroom" for _, k in tl.samples(feb(13, "12:35:13"), feb(13, "12:36:03"), 1)
    ), [(w, k) for w, k in tl.samples(feb(13, "12:35:13"), feb(13, "12:36:03"), 1)]
    assert tl.at(feb(13, "12:36:08")) != "bathroom"


def test_evening_shower_and_exclusive():
    tl = live_timeline("evening.json")
    showering = list(tl.samples(feb(15, "22:11"), feb(15, "22:19")))
    assert all(k == "bathroom:showering" for _, k in showering), showering
    assert tl.at(feb(15, "22:25")).split(":")[0] == "bathroom"
    assert tl.at(feb(15, "22:30")).split(":")[0] == "bathroom"
