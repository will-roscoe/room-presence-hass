"""The presence machine: location, phone arbitration and manual corrections."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.room_presence.engine.machine import LOCATIONS_FIXED, PresenceMachine
from custom_components.room_presence.engine.places import Place
from custom_components.room_presence.engine.types import (
    EngineConfig,
    ManualEvent,
    RoomState,
    StateEvent,
)

T0 = datetime(2000, 1, 3, 9, 0, tzinfo=UTC)
ROOM = "binary_sensor.fp2_room"
BED = "binary_sensor.fp2_bed"
DOOR = "binary_sensor.fp2_door"
MOVE = "binary_sensor.thp_room"
TRACKER = "device_tracker.phone"
SSID = "sensor.phone_ssid"

CFG = EngineConfig(
    fp2_room=ROOM,
    fp2_bed=BED,
    door_witnesses=(DOOR,),
    movement=MOVE,
    phone_tracker=TRACKER,
    phone_ssid=SSID,
)
UNI = Place(slug="uni", name="Uni", ssids=("eduroam",), zones=("zone.uni",))
BUILDING = Place(slug="building", name="Building", ssids=("HallsNet",), on_site=True)


def t(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def machine() -> PresenceMachine:
    return PresenceMachine(CFG, [UNI, BUILDING])


def feed(m: PresenceMachine, seconds: float, entity: str, state: str):
    return m.feed(StateEvent(t(seconds), entity, state))


def held_in_bed(m: PresenceMachine, end: float = 100) -> PresenceMachine:
    for e in (DOOR, MOVE):
        feed(m, 0, e, "off")
    feed(m, 0, SSID, "HallsNet")
    feed(m, 0, ROOM, "on")
    feed(m, 1, BED, "on")
    feed(m, end, BED, "off")
    feed(m, end, ROOM, "off")
    snap = m.snapshot()
    assert snap.location == "bed" and snap.held
    return m


def test_locations_fixed():
    assert LOCATIONS_FIXED == (
        "bed",
        "desk",
        "room",
        "bathroom",
        "sleeping",
        "recently_left",
        "away",
        "unknown",
    )


def test_starts_unknown():
    assert machine().snapshot().location == "unknown"


def test_newer_offsite_match_overrides_held_bed():
    m = held_in_bed(machine())
    trs = feed(m, 500, SSID, "eduroam")
    snap = m.snapshot()
    assert snap.location == "uni"
    assert snap.room_state is RoomState.OUT
    assert snap.place == "uni"
    assert [(tr.at, tr.old, tr.new, tr.reason) for tr in trs] == [(t(500), "bed", "uni", "phone")]


def test_older_offsite_match_does_not_override():
    m = machine()
    feed(m, 0, SSID, "eduroam")  # matched uni before the detection
    held_in_bed(m)
    feed(m, 500, TRACKER, "not_home")
    assert m.snapshot().location == "bed"


def test_detection_beats_phone_and_sets_conflict():
    m = machine()
    feed(m, 0, ROOM, "on")
    feed(m, 10, SSID, "eduroam")
    snap = m.snapshot()
    assert snap.location == "room"
    assert snap.conflict is True


def test_onsite_place_never_overrides():
    m = held_in_bed(machine())
    feed(m, 500, SSID, "Other")
    feed(m, 600, SSID, "HallsNet")
    assert m.snapshot().location == "bed"


def test_not_connected_never_overrides():
    m = held_in_bed(machine())
    feed(m, 500, SSID, "Not Connected")
    feed(m, 600, TRACKER, "not_home")
    assert m.snapshot().location == "bed"


def test_zone_match_overrides_recently_left():
    m = machine()
    feed(m, 0, ROOM, "on")
    feed(m, 40, DOOR, "on")
    feed(m, 70, ROOM, "off")
    assert m.snapshot().location == "recently_left"
    feed(m, 100, TRACKER, "zone.uni")
    assert m.snapshot().location == "uni"


def test_out_shows_place_else_away():
    m = machine()
    feed(m, 0, ROOM, "off")
    assert m.snapshot().location == "away"
    feed(m, 10, SSID, "HallsNet")
    assert m.snapshot().location == "building"
    feed(m, 20, SSID, "Not Connected")
    assert m.snapshot().location == "away"


def test_recently_left_expires_to_place():
    m = machine()
    feed(m, 0, SSID, "HallsNet")
    feed(m, 0, ROOM, "on")
    feed(m, 40, DOOR, "on")
    feed(m, 70, ROOM, "off")
    assert m.next_deadline() == t(340)
    trs = m.tick(t(340))
    assert [(tr.old, tr.new, tr.reason) for tr in trs] == [
        ("recently_left", "building", "recently_left_expired")
    ]


def test_manual_bed_counts_as_detection():
    m = machine()
    feed(m, 0, SSID, "eduroam")
    feed(m, 0, ROOM, "off")
    assert m.snapshot().location == "uni"
    trs = m.feed(ManualEvent(t(100), "bed"))
    assert m.snapshot().location == "bed"
    assert [(tr.old, tr.new, tr.reason) for tr in trs] == [("uni", "bed", "manual")]
    # the uni match is older than the manual detection, so it does not override
    feed(m, 200, TRACKER, "not_home")
    assert m.snapshot().location == "bed"


def test_manual_away_sticks_until_detection():
    m = machine()
    feed(m, 0, SSID, "HallsNet")
    feed(m, 0, ROOM, "on")
    m.feed(ManualEvent(t(10), "away"))
    assert m.snapshot().location == "away"
    feed(m, 20, ROOM, "off")
    feed(m, 30, SSID, "Other")
    feed(m, 40, SSID, "Not Connected")
    assert m.snapshot().location == "away"
    feed(m, 100, ROOM, "on")
    assert m.snapshot().location == "room"


def test_manual_place_ends_on_phone_change():
    m = machine()
    feed(m, 0, ROOM, "off")
    m.feed(ManualEvent(t(10), "uni"))
    assert m.snapshot().location == "uni"
    feed(m, 20, SSID, "HallsNet")
    assert m.snapshot().location == "building"


def test_manual_sleeping_and_recently_left():
    m = machine()
    feed(m, 0, MOVE, "off")
    feed(m, 0, ROOM, "on")
    m.feed(ManualEvent(t(10), "sleeping"))
    assert m.snapshot().location == "sleeping"
    m.feed(ManualEvent(t(20), "recently_left"))
    assert m.snapshot().location == "recently_left"
    assert m.snapshot().sleeping is False


@pytest.mark.parametrize("bad", ["unknown", "office", ""])
def test_manual_rejects_unknown_and_missing_place(bad):
    m = held_in_bed(machine())
    before = m.snapshot()
    with pytest.raises(ValueError, match="unknown state"):
        m.feed(ManualEvent(t(200), bad))
    assert m.snapshot() == before


def test_location_reports_sleeping_over_bed():
    m = machine()
    feed(m, 0, MOVE, "off")
    feed(m, 0, ROOM, "on")
    feed(m, 1, BED, "on")
    trs = m.tick(t(1801))
    assert m.snapshot().location == "sleeping"
    assert m.snapshot().since == t(1)
    assert [(tr.at, tr.old, tr.new, tr.reason) for tr in trs] == [
        (t(1), "bed", "sleeping", "sleep_still")
    ]


def test_phone_override_wakes_sleeper():
    m = machine()
    feed(m, 0, MOVE, "off")
    feed(m, 0, ROOM, "on")
    feed(m, 1, BED, "on")
    m.tick(t(1801))
    feed(m, 2000, BED, "off")
    feed(m, 2000, ROOM, "off")
    feed(m, 3000, SSID, "eduroam")
    snap = m.snapshot()
    assert snap.location == "uni"
    assert snap.sleeping is False
