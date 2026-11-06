<!-- ghtools:sync status START — generated from the status branch, do not edit here -->
<!-- ghtools:sync status END -->

> **Alpha (v0.x).** The rules are tuned on one room and a few days of data, and may change.

A Home Assistant integration that works out where one person is: in their room (on the bed, at the
desk, elsewhere in the room, in the ensuite bathroom), asleep, just left, at a configured place
(uni, the building, ...), or away. An Aqara FP2 presence sensor with zones is the main source;
motion sensors, the phone's Wi-Fi network and its zone fill the gaps.

It exposes, per tracked room:

- `sensor.<name>_location`: `bed`, `desk`, `room`, `bathroom`, `sleeping`, `recently_left`, a place
  slug, `away`, or `unknown`, with attributes `since`, `room_state`, `held`, `place`, `conflict`,
  `reason` and `phone_age`
- binary sensors `<name>_in_room`, `_in_bed`, `_at_desk`, `_in_bathroom`, `_sleeping`,
  `_recently_left`, `_home` (in the room, just left, or at an on-site place) and `_degraded`
- a `room_presence_transition` event (`old`, `new`, `reason`, `at`) on every change
- the action `room_presence.set_state` to correct it (`state`, optional `delay` in seconds)

## Install

Through HACS, as a custom repository: HACS → ⋮ → Custom repositories, add
`https://github.com/will-roscoe/room-presence-hass` with type **Integration**, then download
**Room Presence** and restart Home Assistant. Or copy `custom_components/room_presence` from a
release into the Home Assistant `config/custom_components` directory and restart.

Then add **Room Presence** under Settings → Devices & services.

## Configuration

Sensors are chosen by role. Only the FP2 room zone, one door sensor and the phone tracker are
required.

| Role | Where | Used for |
|---|---|---|
| FP2 room zone | setup | in the room, entering |
| FP2 bed / desk zones | setup | `bed`, `desk` |
| Door sensors (FP2 door zone, a motion sensor aimed at the door) | setup | leaving |
| Phone tracker, phone Wi-Fi network sensor | setup | places |
| Bathroom motion sensor | options | `bathroom` |
| Movement sensor watching the bed | options | `sleeping` |
| Fallback motion sensors | options | entering and leaving while the FP2 is offline |
| Sleep entities (e.g. lights) | options | `sleeping` when they all turn off in bed |

Places are added as entries under the integration (**Add place**): a name, Wi-Fi networks and/or
zones, whether one or both must match, a priority (lower wins) and whether it is on site (part of
home, such as the building). Every timing below is an option.

## How it decides

- **Where in the room:** the most specific FP2 zone that is on: bed, then desk, then room.
- **Leaving needs the door.** When the FP2 stops seeing anyone, it looks back 60 s for a door
  sensor trigger. With none, the person is still there, in the last zone (the FP2 loses people who
  lie still for a while); nothing ever times this out. With one, it is `recently_left` from the door
  trigger, or `bathroom` if the bathroom sensor triggers within 30 s of the door.
- **Coming back:** the FP2 room zone turning on. `recently_left` becomes the place layer after
  5 minutes.
- **Sleeping:** in bed with no movement-sensor trigger for 30 minutes (or the sleep entities all
  turning off), back-dated to the last movement. Movement longer than 8 s, or leaving the bed for
  more than 20 s, wakes.
- **Phone:** the room sensors win while they see someone. While the room is only *held* (no sensor
  sees anyone) or just left, a phone match of an off-site place that is newer than the last
  detection wins. "Not Connected", mobile data and plain `not_home` never count.
- **FP2 offline:** `_degraded` turns on and the state is held. A long door trigger after motion,
  followed by 3 minutes of quiet, is a departure; a door trigger followed by two other sensors
  within 90 s is an arrival.
- **Restart:** the state is rebuilt by replaying the last 24 hours of recorder history (and the
  `set_state` calls made in that time), not restored blindly.

## Development

Needs Python 3.14 (Home Assistant 2026.10).

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt ruff
.venv/bin/pytest
```

The rules live in `custom_components/room_presence/engine/`, which does not import Home Assistant.
`tests/test_replay.py` replays two recorded days (time-shifted, presence sensors only) and checks
the labelled exits, still periods and sleeps.
