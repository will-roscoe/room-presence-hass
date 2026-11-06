"""Engine types: spec defaults and the no-Home-Assistant boundary."""

from datetime import timedelta
from pathlib import Path
import subprocess
import sys

from custom_components.room_presence.engine.types import EngineConfig

ROOT = Path(__file__).resolve().parents[1]


def test_engine_config_defaults_match_spec():
    cfg = EngineConfig(fp2_room="x")
    assert cfg.crossing_window == timedelta(seconds=60)
    assert cfg.bathroom_window == timedelta(seconds=30)
    assert cfg.recently_left_duration == timedelta(minutes=5)
    assert cfg.sleep_quiet == timedelta(minutes=30)
    assert cfg.wake_on_time == timedelta(seconds=8)
    assert cfg.bed_leave_grace == timedelta(seconds=20)
    assert cfg.entry_needs_crossing is False
    assert cfg.exit_pre_window == timedelta(seconds=180)
    assert cfg.exit_quiet == timedelta(minutes=3)
    assert cfg.exit_door_on_time == timedelta(seconds=8)
    assert cfg.entry_window == timedelta(seconds=90)


def test_engine_does_not_import_homeassistant():
    engine = ROOT / "custom_components" / "room_presence" / "engine"
    modules = sorted(p.stem for p in engine.glob("*.py") if p.stem != "__init__")
    assert modules, "engine has no modules"
    # Load the engine package without running custom_components/room_presence/__init__.py,
    # which imports Home Assistant by design.
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
