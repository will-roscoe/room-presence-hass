"""Checks hassfest makes on the manifest, run locally so a release is not stopped by them."""

import json
from pathlib import Path

MANIFEST = Path(__file__).parent.parent / "custom_components" / "room_presence" / "manifest.json"


def test_manifest_keys_sorted():
    keys = list(json.loads(MANIFEST.read_text()))
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])
