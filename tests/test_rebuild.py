"""Startup rebuild: replay recorder history (and stored set_state calls) into a fresh engine."""

import annotationlib
from datetime import timedelta
import inspect
from unittest.mock import patch

from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.room_presence.const import DOMAIN

from .conftest import BED, DESK, INPUTS, PERSON, ROOM, SSID, THP_DOOR, TRACKER, make_entry, setup


@pytest.fixture
def forwardref_signatures():
    """Let autospec read signatures whose annotations name type-checking-only imports.

    The test recorder autospecs recorder functions annotated with names imported only under
    ``TYPE_CHECKING``; on Python 3.14 ``inspect.signature`` evaluates them and raises NameError.
    """
    real = inspect.signature

    def signature(obj, **kwargs):
        kwargs.setdefault("annotation_format", annotationlib.Format.FORWARDREF)
        return real(obj, **kwargs)

    with patch.object(inspect, "signature", signature):
        yield


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    forwardref_signatures, recorder_mock, enable_custom_integrations
):
    """Start the recorder before Home Assistant, then allow custom integrations."""
    yield


class History:
    """Write input states into the recorder at chosen times before ``now``."""

    def __init__(self, hass, freezer) -> None:
        self.hass, self.freezer = hass, freezer
        self.now = dt_util.utcnow()

    def at(self, ago: timedelta, entity: str, state: str, **attrs) -> None:
        self.freezer.move_to(self.now - ago)
        self.hass.states.async_set(entity, state, attrs or None)

    def quiet_start(self, ago: timedelta, presence: str = "off", person: str = "home") -> None:
        for e in INPUTS:
            self.at(ago, e, presence if e == ROOM else "off")
        self.at(ago, PERSON, person, device_trackers=[TRACKER])
        self.at(ago, SSID, "Not Connected")

    async def done(self) -> None:
        self.freezer.move_to(self.now)
        await async_wait_recording_done(self.hass)


H = timedelta(hours=1)
M = timedelta(minutes=1)


def store(hass_storage, entry, *calls) -> str:
    key = f"{DOMAIN}.{entry.entry_id}.manual_v2"
    hass_storage[key] = {"version": 1, "key": key, "data": {"calls": list(calls)}}
    return key


async def test_rebuild_holds_bed_through_dropout(recorder_mock, hass, freezer):
    h = History(hass, freezer)
    h.quiet_start(3 * H)
    h.at(2 * H, ROOM, "on")
    h.at(2 * H - M, BED, "on")
    h.at(20 * M, BED, "off")
    h.at(20 * M, ROOM, "off")
    await h.done()
    entry = make_entry(hass)
    await setup(hass, entry)
    snap = entry.runtime_data.snapshot
    assert snap.key in ("bed", "bed:asleep")
    assert snap.held


async def test_rebuild_detects_exit_in_history(recorder_mock, hass, freezer):
    h = History(hass, freezer)
    h.quiet_start(3 * H)
    h.at(H, ROOM, "on")
    h.at(H - M, DESK, "on")
    h.at(11 * M, DESK, "off")
    h.at(10 * M + timedelta(seconds=30), THP_DOOR, "on")
    h.at(10 * M + timedelta(seconds=25), THP_DOOR, "off")
    h.at(10 * M, ROOM, "off")
    await h.done()
    entry = make_entry(hass)
    await setup(hass, entry)
    assert entry.runtime_data.snapshot.key == "away"
    assert [tr.new for tr in entry.runtime_data.transitions][-2:] == ["just_left", "away"]


async def test_rebuild_applies_stored_manual_event(recorder_mock, hass, freezer, hass_storage):
    h = History(hass, freezer)
    h.quiet_start(3 * H)
    await h.done()
    entry = make_entry(hass)
    at = (h.now - 20 * M).isoformat()
    store(hass_storage, entry, {"at": at, "location": "bed", "activity": None})
    await setup(hass, entry)
    assert entry.runtime_data.snapshot.key == "bed"


async def test_rebuild_drops_manual_events_older_than_lookback(
    recorder_mock, hass, freezer, hass_storage
):
    h = History(hass, freezer)
    h.quiet_start(3 * H)
    await h.done()
    entry = make_entry(hass)
    at = (h.now - 25 * H).isoformat()
    key = store(hass_storage, entry, {"at": at, "location": "bed", "activity": None})
    await setup(hass, entry)
    assert entry.runtime_data.snapshot.key == "home"
    assert hass_storage[key]["data"]["calls"] == []


async def test_stored_manual_for_deleted_room_skipped_on_rebuild(
    recorder_mock, hass, freezer, hass_storage
):
    h = History(hass, freezer)
    h.quiet_start(3 * H)
    await h.done()
    entry = make_entry(hass)
    at = (h.now - 20 * M).isoformat()
    store(hass_storage, entry, {"at": at, "location": "kitchen", "activity": None})
    await setup(hass, entry)
    assert entry.runtime_data.snapshot.key == "home"


async def test_rebuild_unknown_without_presence_history(recorder_mock, hass, freezer):
    h = History(hass, freezer)
    h.quiet_start(3 * H, presence="unavailable", person="unavailable")
    await h.done()
    entry = make_entry(hass)
    await setup(hass, entry)
    snap = entry.runtime_data.snapshot
    assert snap.key == "unknown"
    assert "bedroom" in snap.degraded


async def test_old_offsite_ssid_does_not_override_newer_detection(recorder_mock, hass, freezer):
    h = History(hass, freezer)
    h.quiet_start(80 * H)
    h.at(72 * H, SSID, "eduroam")  # at uni three days ago, never changed since
    h.at(H, ROOM, "on")
    h.at(H - M, BED, "on")
    h.at(50 * M, BED, "off")
    h.at(50 * M, ROOM, "off")
    await h.done()
    entry = make_entry(hass)
    await setup(hass, entry)
    assert entry.runtime_data.snapshot.key in ("bed", "bed:asleep")


async def test_history_read_failure_falls_back_to_current_states(
    recorder_mock, hass, freezer, monkeypatch
):
    from custom_components.room_presence.coordinator import PresenceCoordinator

    async def boom(self, start):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(PresenceCoordinator, "_history", boom)
    h = History(hass, freezer)
    h.quiet_start(H)
    h.at(10 * M, ROOM, "on")
    await h.done()
    entry = make_entry(hass)
    await setup(hass, entry)
    assert entry.runtime_data.snapshot.key == "bedroom"
