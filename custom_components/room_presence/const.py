"""Constants for room_presence: domain, subentry types, configuration keys and timing defaults."""

DOMAIN = "room_presence"
#: Bus event fired on every status change: ``entry_id``, ``old``, ``new``, ``reason``, ``at``.
EVENT_TRANSITION = "room_presence_transition"

SUBENTRY_PERSON = "person"
SUBENTRY_ROOM = "room"
SUBENTRY_PLACE = "place"

# -- home entry -----------------------------------------------------------------------------------
CONF_HOME_ZONE = "home_zone"
CONF_HOME_SSIDS = "home_ssids"

# -- person subentry ------------------------------------------------------------------------------
CONF_PERSON = "person"
CONF_SSID_SENSORS = "ssid_sensors"

# -- room subentry --------------------------------------------------------------------------------
CONF_NAME = "name"
CONF_AREA = "area_id"
CONF_PRESENCE = "presence"
CONF_MOTION = "motion"
CONF_EXITS = "exits"
CONF_BATHROOM = "bathroom"
CONF_EXCLUSIVE = "exclusive"
CONF_HUMIDITY = "humidity"
CONF_SPOTS = "spots"
# -- spot (inside a room subentry) ----------------------------------------------------------------
CONF_SENSOR = "sensor"
CONF_ROLE = "role"
CONF_STILLNESS = "stillness"
CONF_SLEEP_ENTITIES = "sleep_entities"
ROLE_BED = "bed"

# -- place subentry -------------------------------------------------------------------------------
CONF_SSIDS = "ssids"
CONF_ZONES = "zones"
CONF_MODE = "mode"
CONF_PRIORITY = "priority"

# -- options --------------------------------------------------------------------------------------
#: Option keys holding a duration in seconds; each is the :class:`~.engine.types.Timings` field of
#: the same name.
TIMING_KEYS = (
    "crossing_window",
    "move_window",
    "recently_left_duration",
    "sleep_quiet",
    "wake_on_time",
    "bed_leave_grace",
    "exit_pre_window",
    "exit_quiet",
    "exit_door_on_time",
    "entry_window",
    "exclusive_quiet",
    "shower_window",
    "shower_end",
)
#: Humidity points above the recent low that start a shower.
CONF_SHOWER_RISE = "shower_rise"
CONF_ENTRY_NEEDS_CROSSING = "entry_needs_crossing"
#: Hours of recorder history replayed at startup.
CONF_REBUILD_LOOKBACK = "rebuild_lookback"

#: Option defaults: seconds for :data:`TIMING_KEYS`, points for the shower rise, hours for the
#: rebuild look-back.
DEFAULTS: dict[str, float | bool] = {
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
    CONF_SHOWER_RISE: 6.0,
    CONF_ENTRY_NEEDS_CROSSING: False,
    CONF_REBUILD_LOOKBACK: 24,
}

# -- version 1 entries (one entry per room), read only by the migration ---------------------------
V1_FP2_ROOM = "fp2_room"
V1_FP2_BED = "fp2_bed"
V1_FP2_DESK = "fp2_desk"
V1_DOOR_WITNESSES = "door_witnesses"
V1_BATHROOM = "bathroom"
V1_MOVEMENT = "movement"
V1_FALLBACK_MOTION = "fallback_motion"
V1_SLEEP_ENTITIES = "sleep_entities"
V1_PHONE_TRACKER = "phone_tracker"
V1_PHONE_SSID = "phone_ssid"
V1_BATHROOM_WINDOW = "bathroom_window"
V1_ON_SITE = "on_site"
