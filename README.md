<!-- ghtools:sync status START — generated from the status branch, do not edit here -->
<!-- ghtools:sync status END -->

> **Alpha (v0.x).** The rules are tuned on one home and a few weeks of data, and may change.

A Home Assistant integration that works out where one person is in their home and what they are
doing: which room, which spot in it (the bed, the desk, the sofa), whether they are asleep or in
the shower, whether they have just left, and where they are once out (a place you define, a Home
Assistant zone, or away). Presence sensors in each room (mmWave zones work best) are the main
source; motion sensors, door sensors, a humidity sensor, the person's zone and their phone's Wi-Fi
network fill the gaps.

## What you get

**A device for the person**, named after them (here a person called Alex):

- `sensor.alex_location`: the most specific place known, by name: a spot, else a room, else
  `Home`, else a place or zone, else `Away` (`Unknown` when nothing is known). Attributes `since`,
  `room`, `spot`, `held`, `place`, `conflict`, `reason` and `phone_age`.
- `sensor.alex_activity`: `asleep`, `showering`, `awake`, `away` or `unknown`.
- `sensor.alex_state`: a readable summary for dashboards.
- Binary sensors `binary_sensor.alex_home`, `_just_left`, `_asleep` and `_showering`.

| Situation | location | activity | state |
|---|---|---|---|
| In bed, asleep | Bed | asleep | Bed (asleep) |
| At the desk | Desk | awake | Desk |
| In the bedroom, at no spot | Bedroom | awake | Bedroom |
| In the bathroom, showering | Bathroom | showering | Bathroom (showering) |
| Just left (first 5 min after an exit) | Home | awake | Just left |
| Home by zone or Wi-Fi, in no room | Home | awake | Home |
| At a place called Office | Office | away | Office |
| Out, in no zone or place | Away | away | Away |
| Nothing known | Unknown | unknown | Unknown |

**A device for each room**, in the room's area if you pick one:

- `binary_sensor.bedroom_occupied`: the person is in the room (or held there, see below).
- One binary sensor per spot, e.g. `binary_sensor.bedroom_bed`, `binary_sensor.bedroom_desk`.
- `binary_sensor.bedroom_degraded` (diagnostic): every presence sensor of the room is unavailable;
  attribute `entities` lists them.

Home Assistant builds new entity ids from the area, the device and the entity, so a room device in
an area gets ids such as `binary_sensor.bedroom_bedroom_occupied`. Rename them freely; the
integration tracks entities by their unique id.

Also: a `room_presence_transition` event (`entry_id`, `old`, `new`, `reason`, `at`) on every
change, the `room_presence.set_state` action (below), and a diagnostics download.

## Install

Through HACS, as a custom repository: HACS → ⋮ → Custom repositories, add
`https://github.com/will-roscoe/room-presence-hass` with type **Integration**, then download
**Room Presence** and restart Home Assistant. Or copy `custom_components/room_presence` from a
release into the Home Assistant `config/custom_components` directory and restart.

## Setup

Add **Room Presence** under Settings → Devices & services. There is one home per Home Assistant.

1. **The home:** the person to track, the home zone (default `zone.home`) and, optionally, your home
   Wi-Fi network names.
2. **Phone Wi-Fi sensors:** sensors reporting the network the person's phone is on. They are found
   from the person's device trackers (e.g. the Companion app's Wi-Fi connection sensor); untick any
   you do not want.
3. **Rooms** (**Add room** on the integration page), one per room:

   | Field | Required | Used for |
   |---|---|---|
   | Presence sensors | yes | the room is occupied while any is on |
   | Motion sensors | no | entering and leaving while the presence sensors are unavailable |
   | Exit sensors | no | doors out of the home; leaving the home is only recognised from rooms with exit sensors |
   | Bathroom | no | detect showers from a humidity sensor (asked next) |
   | Exclusive | no | the room wins over neighbours whose sensors see into it (see below) |
   | Area | no | where the room's device goes |

   Then add **spots**: a name, the sensor that is on while someone is there, and a role. A spot
   with the bed role also takes a movement sensor watching the bed and, optionally, sleep
   entities (e.g. lights). Spots are checked in list order; move one up to give it priority.
4. **Places** (**Add place**, optional): somewhere outside the home, matched by Wi-Fi networks
   and/or zones (either one or both must match), with a priority (lower wins). Home Assistant zones
   already show by their name without a place; add one to match on Wi-Fi or to rename a zone.

Every timing is an option (Configure). Room, place and person settings are changed with each
entry's own configure button. Names must be unique across rooms, spots and places, and cannot be
`home`, `away`, `unknown` or `just_left`.

## How it decides

- **Rooms and spots.** A room is occupied while any of its presence or spot sensors is on. The spot
  is the first spot in the list whose sensor is on.
- **Entering.** From out, the first room *with exit sensors* to become occupied (a single blip in an
  inner room never proves someone came home); rooms without exit sensors are reached by moving from
  another room. From unknown, the first room to become occupied.
- **Moving between rooms.** When a room empties, another room with a presence edge from 60 s before
  to 30 s after (the move window) that is occupied is where the person went. A neighbouring room
  turning on while the current room is still occupied is ignored as sensor bleed.
- **Exclusive rooms** (typically a bathroom next to a bedroom whose sensor sees through the wall):
  a presence edge there moves the person at once, even with the current room occupied. They move
  back on a new edge in the room they came from, after 2 minutes of quiet in the exclusive room, or
  on an exit.
- **Leaving the home.** When a room empties with no move, it looks back 60 s for one of its exit
  sensors. With one, the person has just left (back-dated to that trigger), and is out after
  5 minutes. With none, the person is **held** in the room: sensors lose people who lie still, and
  nothing times this out.
- **Sensors offline.** While all of a room's presence sensors are unavailable, its `degraded`
  sensor turns on and the room falls back to its motion and exit sensors: a long exit trigger after
  motion followed by 3 minutes of quiet is a departure; an exit trigger followed by two other
  sensors (one, if only one is available) within 90 s is an arrival.
- **Asleep.** On a bed spot with no movement for 30 minutes (or every sleep entity turning off),
  back-dated to the last movement. Movement longer than 8 s, or leaving the bed for more than 20 s,
  wakes.
- **Showering.** In a bathroom room with a humidity sensor: the humidity rises at least 6 points
  above its lowest reading of the last 15 minutes, with someone entering the room in that time.
  While showering the person stays in the bathroom whatever the neighbouring sensors say. It ends
  when the humidity sets no new high for 3 minutes, or on an exit.
- **Home or away.** The person is home while in, held in or just left a room, while their person
  entity is in the home zone, or while their phone is on a home Wi-Fi network. Once someone has
  left, the **newest evidence wins**: only a zone change or Wi-Fi report *after* the exit brings
  them home again, so a phone that has said Home all along does not hide that they went out.
  Out, the location is the best matching place, else the zone, else Away.
- **The phone overrides a hold.** While held or just left, a newer match of an off-site zone or
  place sends the person out. `not_home`, "Not Connected" and mobile data never count.
- **Restart.** The state is rebuilt by replaying the last 24 hours of recorder history (and the
  `set_state` calls made in that time), not restored blindly.

## Correcting it

`room_presence.set_state` sets the location and/or the activity, now or after `delay` seconds:

```yaml
action: room_presence.set_state
data:
  location: bed      # a room, spot or place name as a slug, just_left or away
  activity: asleep   # asleep or awake; asleep alone means the bed
  delay: 60
```

A new call cancels a pending one. Inference continues from the corrected state.

## Upgrading from 0.1

Automatic. The 0.1 room becomes a room named after the old entry, with Bed and Desk spots, its door
sensors as exit sensors, and its fallback motion sensors; a configured bathroom sensor becomes an
exclusive Bathroom room (pick its humidity sensor afterwards to detect showers). The person is the
one whose trackers include the old phone tracker; if none or several match, a repair asks you to
pick one. Places and timings carry over.

The entity ids change: the old `sensor.<name>_location` and its binary sensors are removed, and the
new person and room entities are created. Update any automation that used them. A second 0.1 entry
is not upgraded; a repair explains how to remove it.

## Development

Needs Python 3.14 (Home Assistant 2026.10).

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt ruff
.venv/bin/pytest
```

The rules live in `custom_components/room_presence/engine/`, which does not import Home Assistant.
`tests/test_replay.py` replays recorded days (time-shifted) and checks the labelled exits, still
periods, sleeps and showers.
