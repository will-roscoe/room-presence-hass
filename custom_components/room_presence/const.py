"""Constants for room_presence: domain, option keys and their defaults."""

DOMAIN = "room_presence"
#: Bus event fired on every ``location`` change: ``entry_id``, ``old``, ``new``, ``reason``, ``at``
EVENT_TRANSITION = "room_presence_transition"

CONF_NAME = "name"
CONF_FP2_ROOM = "fp2_room"
CONF_FP2_BED = "fp2_bed"
CONF_FP2_DESK = "fp2_desk"
CONF_DOOR_WITNESSES = "door_witnesses"
CONF_BATHROOM = "bathroom"
CONF_MOVEMENT = "movement"
CONF_FALLBACK_MOTION = "fallback_motion"
CONF_SLEEP_ENTITIES = "sleep_entities"
CONF_PHONE_TRACKER = "phone_tracker"
CONF_PHONE_SSID = "phone_ssid"
CONF_CROSSING_WINDOW = "crossing_window"
CONF_BATHROOM_WINDOW = "bathroom_window"
CONF_RECENTLY_LEFT_DURATION = "recently_left_duration"
CONF_SLEEP_QUIET = "sleep_quiet"
CONF_WAKE_ON_TIME = "wake_on_time"
CONF_BED_LEAVE_GRACE = "bed_leave_grace"
CONF_ENTRY_NEEDS_CROSSING = "entry_needs_crossing"
CONF_EXIT_PRE_WINDOW = "exit_pre_window"
CONF_EXIT_QUIET = "exit_quiet"
CONF_EXIT_DOOR_ON_TIME = "exit_door_on_time"
CONF_ENTRY_WINDOW = "entry_window"
#: Hours of recorder history replayed at startup.
CONF_REBUILD_LOOKBACK = "rebuild_lookback"

#: Timing defaults in seconds (``rebuild_lookback`` in hours), and the entry option flag.
DEFAULTS: dict[str, float | bool] = {
    CONF_CROSSING_WINDOW: 60,
    CONF_BATHROOM_WINDOW: 30,
    CONF_RECENTLY_LEFT_DURATION: 300,
    CONF_SLEEP_QUIET: 1800,
    CONF_WAKE_ON_TIME: 8,
    CONF_BED_LEAVE_GRACE: 20,
    CONF_ENTRY_NEEDS_CROSSING: False,
    CONF_EXIT_PRE_WINDOW: 180,
    CONF_EXIT_QUIET: 180,
    CONF_EXIT_DOOR_ON_TIME: 8,
    CONF_ENTRY_WINDOW: 90,
    CONF_REBUILD_LOOKBACK: 24,
}
