"""Engine types: timings, configuration validation, routing tables and the status key."""

from datetime import timedelta
from pathlib import Path
import subprocess
import sys

import pytest

from custom_components.room_presence.engine.types import (
    Activity,
    HomeConfig,
    Presence,
    RoomConfig,
    SpotConfig,
    Timings,
    status_key,
)
from tests.homes import FP2, HUM, LIVE, THP, bathroom, bedroom, home

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_SECONDS = {
    "crossing_window": 60,
    "move_window": 30,
    "recently_left_duration": 300,
    "sleep_quiet": 1800,
    "wake_on_time": 8,
    "bed_leave_grace": 20,
    "exit_pre_window": 180,
    "exit_quiet": 180,
    "exit_door_on_time": 8,
    "entry_window": 90,
    "exclusive_quiet": 120,
    "shower_window": 900,
    "shower_end": 180,
}


def test_timings_defaults():
    t = Timings()
    for name, seconds in DEFAULT_SECONDS.items():
        assert getattr(t, name) == timedelta(seconds=seconds), name
    assert t.shower_rise == 6.0
    assert t.entry_needs_crossing is False
    assert t.longest_lookback == timedelta(minutes=15)
    assert Timings(shower_window=timedelta(minutes=40)).longest_lookback == timedelta(minutes=40)


@pytest.mark.parametrize(
    ("location", "activity", "presence", "key"),
    [
        ("bed", Activity.ASLEEP, Presence.IN_ROOM, "bed:asleep"),
        ("bathroom", Activity.SHOWERING, Presence.IN_ROOM, "bathroom:showering"),
        ("home", Activity.AWAKE, Presence.JUST_LEFT, "just_left"),
        ("desk", Activity.AWAKE, Presence.IN_ROOM, "desk"),
        ("away", Activity.AWAY, Presence.OUT, "away"),
    ],
)
def test_status_key(location, activity, presence, key):
    assert status_key(location, activity, presence) == key


def _room(slug: str, **kw) -> RoomConfig:
    return RoomConfig(
        slug=slug, name=slug.title(), presence=kw.pop("presence", ("p." + slug,)), **kw
    )


@pytest.mark.parametrize(
    "rooms",
    [
        pytest.param((_room("a", presence=()),), id="no-presence"),
        pytest.param((_room("home"),), id="reserved-room-slug"),
        pytest.param(
            (_room("a", spots=(SpotConfig("just_left", "Just left", "s.x"),)),), id="reserved-spot"
        ),
        pytest.param(
            (
                _room("a", spots=(SpotConfig("desk", "Desk", "s.a"),)),
                _room("b", spots=(SpotConfig("desk", "Desk", "s.b"),)),
            ),
            id="duplicate-spot",
        ),
        pytest.param((_room("a", spots=(SpotConfig("a", "A", "s.a"),)),), id="spot-equals-room"),
        pytest.param((_room("a", humidity="sensor.h"),), id="humidity-without-bathroom"),
    ],
)
def test_home_config_rejects(rooms):
    with pytest.raises(ValueError):
        HomeConfig(rooms=rooms)


def test_owners_and_inputs():
    assert ("bedroom", frozenset({"exit", "motion"})) in LIVE.owners(THP.format(3))
    assert LIVE.owners(HUM) == (("bathroom", frozenset({"humidity"})),)
    assert LIVE.owners("sensor.nothing") == ()
    inputs = LIVE.inputs()
    assert len(inputs) == len(set(inputs))
    for e in (*(FP2.format(z) for z in ("room", "bed", "desk", "door")), HUM):
        assert e in inputs
    for i in (1, 2, 3, 4):
        assert THP.format(i) in inputs
    assert inputs[-1] == "person.p"


def test_inputs_end_with_person_then_ssid_sensors():
    h = home(bedroom(), bathroom(), ssid_sensors=("sensor.phone_ssid",))
    assert h.inputs()[-2:] == ("person.p", "sensor.phone_ssid")


def test_room_lookup():
    assert LIVE.room("bathroom").exclusive is True
    with pytest.raises(KeyError):
        LIVE.room("kitchen")


def test_engine_has_no_homeassistant_imports():
    engine = ROOT / "custom_components" / "room_presence" / "engine"
    modules = sorted(
        ".".join(p.relative_to(engine).with_suffix("").parts)
        for p in engine.rglob("*.py")
        if p.stem != "__init__"
    )
    assert modules, "engine has no modules"
    code = (
        "import importlib.util, sys\n"
        f"pkg = {str(engine)!r}\n"
        "spec = importlib.util.spec_from_file_location('engine', pkg + '/__init__.py',"
        " submodule_search_locations=[pkg])\n"
        "mod = importlib.util.module_from_spec(spec); sys.modules['engine'] = mod\n"
        "spec.loader.exec_module(mod)\n"
        f"for m in {modules!r}: importlib.import_module('engine.' + m)\n"
        "bad = [k for k in sys.modules if k == 'homeassistant' or k.startswith('homeassistant.')]\n"
        "print(bad); sys.exit(1 if bad else 0)\n"
    )
    res = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
