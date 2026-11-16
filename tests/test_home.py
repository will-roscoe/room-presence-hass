"""The home machine: status key, home evidence, places and zones, phone override, manual calls."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.room_presence.engine.home import HomeMachine
from custom_components.room_presence.engine.places import Place
from custom_components.room_presence.engine.types import (
    Activity,
    ManualEvent,
    Presence,
    RoomConfig,
    StateEvent,
)
from tests.homes import FP2, HUM, LIVE, THP, bedroom, home

T0 = datetime(2000, 2, 3, 9, 0, tzinfo=UTC)
ROOM, BED, DOOR = FP2.format("room"), FP2.format("bed"), FP2.format("door")
MOVE, BATH = THP.format(1), THP.format(2)
PERSON = "person.p"
SSID = "sensor.phone_ssid"

HOME = home(
    bedroom(desk=False, exits=(DOOR,), stillness=MOVE),
    ssid_sensors=(SSID,),
    home_ssids=frozenset({"HallsNet"}),
    zone_names={"zone.uni_of_lancashire": "Uni of Lancashire"},
)
UNI = Place(slug="uni", name="Uni", ssids=("eduroam",), zones=("zone.uni",))


def t(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def machine(h=HOME, places=(UNI,)) -> HomeMachine:
    return HomeMachine(h, list(places))


def feed(m: HomeMachine, seconds: float, entity: str, state: str):
    return m.feed(StateEvent(t(seconds), entity, state))


def manual(m: HomeMachine, seconds: float, location=None, activity=None):
    return m.feed(ManualEvent(t(seconds), location=location, activity=activity))


def held_in_bed(m: HomeMachine, end: float = 100) -> HomeMachine:
    for e in (DOOR, MOVE):
        feed(m, 0, e, "off")
    feed(m, 0, SSID, "HallsNet")
    feed(m, 0, ROOM, "on")
    feed(m, 1, BED, "on")
    feed(m, end, BED, "off")
    feed(m, end, ROOM, "off")
    snap = m.snapshot()
    assert snap.key == "bed" and snap.held
    return m


def exited(m: HomeMachine) -> HomeMachine:
    feed(m, 0, ROOM, "on")
    feed(m, 40, DOOR, "on")
    feed(m, 45, DOOR, "off")
    feed(m, 70, ROOM, "off")
    m.tick(t(100))
    assert m.snapshot().key == "just_left"
    return m


def test_starts_unknown():
    snap = machine().snapshot()
    assert snap.key == "unknown"
    assert snap.activity is Activity.UNKNOWN
    assert snap.location_name is None


def test_newer_offsite_match_overrides_held_bed():
    m = held_in_bed(machine())
    trs = feed(m, 500, SSID, "eduroam")
    snap = m.snapshot()
    assert snap.key == "uni"
    assert snap.presence is Presence.OUT
    assert snap.place == "uni"
    assert snap.location_name == "Uni"
    assert [(tr.at, tr.old, tr.new, tr.reason) for tr in trs] == [(t(500), "bed", "uni", "phone")]


def test_older_offsite_match_does_not_override():
    m = machine()
    feed(m, 0, SSID, "eduroam")  # matched uni before the detection
    feed(m, 0, ROOM, "on")
    feed(m, 1, BED, "on")
    feed(m, 100, BED, "off")
    feed(m, 100, ROOM, "off")
    feed(m, 500, PERSON, "not_home")
    assert m.snapshot().key == "bed"
    assert m.snapshot().conflict is False


def test_detection_beats_phone_and_sets_conflict():
    m = machine()
    feed(m, 0, ROOM, "on")
    feed(m, 10, SSID, "eduroam")
    snap = m.snapshot()
    assert snap.key == "bedroom"
    assert snap.location_name == "Bedroom"
    assert snap.conflict is True


def test_home_ssid_never_overrides():
    m = held_in_bed(machine())
    feed(m, 500, SSID, "Other")
    feed(m, 600, SSID, "HallsNet")
    assert m.snapshot().key == "bed"


def test_not_connected_never_overrides():
    m = held_in_bed(machine())
    feed(m, 500, SSID, "Not Connected")
    feed(m, 600, PERSON, "not_home")
    assert m.snapshot().key == "bed"


def test_zone_match_overrides_just_left():
    m = exited(machine())
    feed(m, 110, PERSON, "zone.uni")
    assert m.snapshot().key == "uni"


def test_out_shows_home_ssid_else_away():
    m = machine()
    feed(m, 0, ROOM, "off")
    assert m.snapshot().key == "away"
    feed(m, 10, SSID, "HallsNet")
    assert m.snapshot().key == "home"
    assert m.snapshot().activity is Activity.AWAKE
    feed(m, 20, SSID, "Not Connected")
    assert m.snapshot().key == "away"


def test_just_left_expires_to_away_with_stale_home_ssid():
    m = machine()
    feed(m, 0, SSID, "HallsNet")
    exited(m)
    assert m.snapshot().location == "home"
    assert m.next_deadline() == t(340)
    trs = m.tick(t(340))
    assert [(tr.old, tr.new, tr.reason) for tr in trs] == [
        ("just_left", "away", "just_left_expired")
    ]


def test_away_after_exit_with_stale_home_zone():
    m = machine()
    feed(m, 0, PERSON, "zone.home")
    exited(m)
    m.tick(t(340))
    snap = m.snapshot()
    assert snap.key == "away"
    assert snap.activity is Activity.AWAY
    assert snap.home is False


def test_home_when_zone_newly_home_after_exit():
    m = machine()
    feed(m, 0, PERSON, "zone.home")
    exited(m)
    m.tick(t(340))
    feed(m, 900, PERSON, "not_home")
    assert m.snapshot().key == "away"
    feed(m, 1000, PERSON, "zone.home")
    assert m.snapshot().key == "home"
    assert m.snapshot().home is True


def test_home_ssid_counts_after_exit_only_when_newer():
    m = machine()
    feed(m, 0, SSID, "HallsNet")
    exited(m)
    m.tick(t(340))
    assert m.snapshot().key == "away"
    feed(m, 400, SSID, "Other")
    feed(m, 500, SSID, "hallsnet ")  # compared after trimming and case-folding
    assert m.snapshot().key == "home"


def test_home_without_any_exit():
    m = machine()
    feed(m, 0, PERSON, "zone.home")
    feed(m, 1, ROOM, "off")
    snap = m.snapshot()
    assert (snap.key, snap.activity, snap.home) == ("home", Activity.AWAKE, True)


def test_zone_location_and_name():
    m = machine()
    feed(m, 0, ROOM, "off")
    feed(m, 10, PERSON, "zone.uni_of_lancashire")
    snap = m.snapshot()
    assert snap.location == "uni_of_lancashire"
    assert snap.location_name == "Uni of Lancashire"
    assert snap.activity is Activity.AWAY
    assert m.names()["uni_of_lancashire"] == "Uni of Lancashire"


def test_unknown_zone_uses_object_id():
    m = machine()
    feed(m, 0, ROOM, "off")
    feed(m, 10, PERSON, "zone.cafe")
    snap = m.snapshot()
    assert (snap.location, snap.location_name) == ("cafe", "cafe")


def test_person_unavailable_rooms_still_work():
    m = machine()
    feed(m, 0, PERSON, "unavailable")
    assert m.snapshot().key == "unknown"
    exited(m)
    m.tick(t(340))
    assert m.snapshot().key == "away"


def test_just_left_reads_home_and_awake():
    m = exited(machine())
    snap = m.snapshot()
    assert (snap.location, snap.activity, snap.home) == ("home", Activity.AWAKE, True)


def test_manual_bed_counts_as_detection():
    m = machine()
    feed(m, 0, SSID, "eduroam")
    feed(m, 0, ROOM, "off")
    assert m.snapshot().key == "uni"
    trs = manual(m, 100, location="bed")
    assert m.snapshot().key == "bed"
    assert [(tr.old, tr.new, tr.reason) for tr in trs] == [("uni", "bed", "manual")]
    feed(m, 200, PERSON, "not_home")
    assert m.snapshot().key == "bed"


def test_manual_away_sticks_until_detection():
    m = machine()
    feed(m, 0, SSID, "HallsNet")
    feed(m, 0, ROOM, "on")
    manual(m, 10, location="away")
    assert m.snapshot().key == "away"
    feed(m, 20, ROOM, "off")
    feed(m, 30, SSID, "Other")
    feed(m, 40, SSID, "Not Connected")
    assert m.snapshot().key == "away"
    feed(m, 100, ROOM, "on")
    assert m.snapshot().key == "bedroom"


def test_manual_place_ends_on_new_home_evidence():
    m = machine()
    feed(m, 0, ROOM, "off")
    manual(m, 10, location="uni")
    assert m.snapshot().key == "uni"
    feed(m, 20, SSID, "HallsNet")
    assert m.snapshot().key == "home"


def test_manual_asleep_and_just_left():
    m = machine()
    feed(m, 0, MOVE, "off")
    feed(m, 0, ROOM, "on")
    manual(m, 10, activity="asleep")
    assert m.snapshot().key == "bed:asleep"
    manual(m, 20, location="just_left")
    snap = m.snapshot()
    assert snap.key == "just_left"
    assert snap.asleep is False


def test_manual_awake_wakes():
    m = machine()
    feed(m, 0, MOVE, "off")
    feed(m, 0, ROOM, "on")
    manual(m, 10, activity="asleep")
    manual(m, 20, activity="awake")
    assert m.snapshot().key == "bed"


@pytest.mark.parametrize(
    ("location", "activity"),
    [("unknown", None), ("office", None), ("", None), (None, None), (None, "showering")],
)
def test_manual_validation(location, activity):
    m = held_in_bed(machine())
    before = m.snapshot()
    with pytest.raises(ValueError):
        manual(m, 200, location=location, activity=activity)
    assert m.snapshot() == before
    with pytest.raises(ValueError):
        m.check_manual(location, activity)


def test_check_manual_accepts():
    m = machine()
    for location in ("bedroom", "bed", "just_left", "away", "uni"):
        m.check_manual(location, None)
    m.check_manual(None, "asleep")
    m.check_manual("bed", "awake")


def test_location_reports_asleep_over_bed():
    m = machine()
    feed(m, 0, MOVE, "off")
    feed(m, 0, ROOM, "on")
    feed(m, 1, BED, "on")
    trs = m.tick(t(1801))
    snap = m.snapshot()
    assert snap.key == "bed:asleep"
    assert snap.asleep is True
    assert snap.since == t(1)
    assert [(tr.at, tr.old, tr.new, tr.reason) for tr in trs] == [
        (t(1), "bed", "bed:asleep", "sleep_still")
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
    assert snap.key == "uni"
    assert snap.asleep is False


def test_showering_pins_and_keys():
    m = machine(LIVE, ())
    for e in (THP.format(1), THP.format(3), THP.format(4), BATH, DOOR, BED, FP2.format("desk")):
        feed(m, 0, e, "off")
    feed(m, 0, ROOM, "on")
    feed(m, 0, HUM, "37")
    feed(m, 360, BATH, "on")  # exclusive bathroom claims the person
    assert m.snapshot().key == "bathroom"
    feed(m, 380, BATH, "off")
    feed(m, 400, ROOM, "off")
    feed(m, 405, ROOM, "on")  # the bedroom sensor sees through the wall: back to the bedroom
    assert m.snapshot().key == "bedroom"
    feed(m, 540, HUM, "39")
    trs = feed(m, 660, HUM, "45")  # +8 within 15 min of the bathroom edge: showering
    snap = m.snapshot()
    assert snap.key == "bathroom:showering"
    assert snap.showering and snap.activity is Activity.SHOWERING
    assert trs[-1].at == t(360)  # back-dated to the bathroom presence edge
    feed(m, 700, BED, "on")  # pinned: the bedroom is ignored while showering
    feed(m, 701, BED, "off")
    assert m.snapshot().key == "bathroom:showering"
    feed(m, 840, HUM, "53")
    m.tick(t(840 + 180))
    assert m.snapshot().key == "bathroom"
    feed(m, 1100, BED, "on")
    assert m.snapshot().key == "bed"


def test_entity_shared_by_two_rooms_reaches_both():
    shared = THP.format(2)
    h = home(
        bedroom(desk=False, motion=(shared,)),
        RoomConfig("bathroom", "Bathroom", presence=(shared,), bathroom=True),
    )
    m = machine(h, ())
    feed(m, 0, shared, "on")
    assert m.rooms["bedroom"].state_of(shared) == "on"
    assert m.rooms["bathroom"].state_of(shared) == "on"


def test_degraded_rooms_in_snapshot():
    m = machine(LIVE, ())
    feed(m, 0, ROOM, "unavailable")
    feed(m, 0, BATH, "off")
    assert m.snapshot().degraded == frozenset({"bedroom"})
