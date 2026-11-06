"""Place matching from the phone's SSID and zone."""

import pytest

from custom_components.room_presence.engine.places import Place, match_place

UNI = Place(slug="uni", name="Uni", ssids=("eduroam",), zones=("zone.uni",))
BUILDING = Place(slug="building", name="Building", ssids=("HallsNet",), on_site=True)


def test_any_mode_matches_ssid_or_zone():
    assert match_place([UNI], "eduroam", None) is UNI
    assert match_place([UNI], None, "zone.uni") is UNI


def test_all_mode_needs_both():
    p = Place(slug="lab", name="Lab", ssids=("eduroam",), zones=("zone.uni",), mode="all")
    assert match_place([p], "eduroam", None) is None
    assert match_place([p], None, "zone.uni") is None
    assert match_place([p], "eduroam", "zone.uni") is p


def test_priority_lower_wins():
    a = Place(slug="a", name="A", ssids=("x",), priority=1)
    b = Place(slug="b", name="B", ssids=("x",), priority=0)
    assert match_place([a, b], "x", None) is b


def test_equal_priority_keeps_list_order():
    a = Place(slug="a", name="A", ssids=("x",))
    b = Place(slug="b", name="B", ssids=("x",))
    assert match_place([a, b], "x", None) is a


def test_no_match_returns_none():
    assert match_place([UNI, BUILDING], "Starbucks", "zone.work") is None


@pytest.mark.parametrize(
    "ssid", [None, "", "Not Connected", "unavailable", "unknown", "<not connected>"]
)
@pytest.mark.parametrize("zone", [None, "not_home"])
def test_meaningless_phone_states_never_match(ssid, zone):
    weird = Place(slug="w", name="W", ssids=(ssid or "",), zones=("not_home",))
    assert match_place([UNI, BUILDING, weird], ssid, zone) is None


def test_place_without_ssids_or_zones_never_matches():
    assert match_place([Place(slug="e", name="E")], "eduroam", "zone.uni") is None
