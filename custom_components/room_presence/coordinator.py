"""Home Assistant glue: live input changes into the engine, deadlines, set_state, publishing."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util, slugify

from .const import (
    CONF_BATHROOM,
    CONF_DOOR_WITNESSES,
    CONF_ENTRY_NEEDS_CROSSING,
    CONF_FALLBACK_MOTION,
    CONF_FP2_BED,
    CONF_FP2_DESK,
    CONF_FP2_ROOM,
    CONF_MOVEMENT,
    CONF_PHONE_SSID,
    CONF_PHONE_TRACKER,
    CONF_REBUILD_LOOKBACK,
    CONF_SLEEP_ENTITIES,
    DEFAULTS,
    DOMAIN,
    EVENT_TRANSITION,
)
from .engine import EngineConfig, ManualEvent, Snapshot, StateEvent, Transition
from .engine.machine import PresenceMachine
from .engine.places import Place
from .engine.replay import history_events, replay

#: Option keys holding a duration in seconds, by EngineConfig field (the same names).
TIMING_KEYS = (
    "crossing_window",
    "bathroom_window",
    "recently_left_duration",
    "sleep_quiet",
    "wake_on_time",
    "bed_leave_grace",
    "exit_pre_window",
    "exit_quiet",
    "exit_door_on_time",
    "entry_window",
)
SUBENTRY_PLACE = "place"
_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1


def engine_config(conf: Mapping[str, Any]) -> EngineConfig:
    """Build an :class:`EngineConfig` from merged entry data and options (seconds → timedelta)."""

    def opt(key: str) -> str | None:
        return conf.get(key) or None

    def many(key: str) -> tuple[str, ...]:
        return tuple(conf.get(key) or ())

    timings = {k: timedelta(seconds=float(conf.get(k, DEFAULTS[k]))) for k in TIMING_KEYS}
    return EngineConfig(
        fp2_room=conf[CONF_FP2_ROOM],
        fp2_bed=opt(CONF_FP2_BED),
        fp2_desk=opt(CONF_FP2_DESK),
        door_witnesses=many(CONF_DOOR_WITNESSES),
        bathroom=opt(CONF_BATHROOM),
        movement=opt(CONF_MOVEMENT),
        fallback_motion=many(CONF_FALLBACK_MOTION),
        sleep_entities=many(CONF_SLEEP_ENTITIES),
        phone_tracker=opt(CONF_PHONE_TRACKER),
        phone_ssid=opt(CONF_PHONE_SSID),
        entry_needs_crossing=bool(
            conf.get(CONF_ENTRY_NEEDS_CROSSING, DEFAULTS[CONF_ENTRY_NEEDS_CROSSING])
        ),
        **timings,
    )


def entry_places(entry: ConfigEntry) -> list[Place]:
    """Return the entry's place subentries as :class:`Place` objects (slug = slugified name)."""
    out = []
    for sub in entry.subentries.values():
        if sub.subentry_type != SUBENTRY_PLACE:
            continue
        d = sub.data
        out.append(
            Place(
                slug=slugify(d["name"]),
                name=d["name"],
                ssids=tuple(d.get("ssids") or ()),
                zones=tuple(d.get("zones") or ()),
                mode=d.get("mode", "any"),
                priority=int(d.get("priority", 0)),
                on_site=bool(d.get("on_site", False)),
            )
        )
    return out


class PresenceCoordinator:
    """Runs one entry's :class:`PresenceMachine` against live Home Assistant state.

    Read :attr:`snapshot` for the answer, :attr:`transitions` and :attr:`edges` for the recent
    log; :meth:`add_listener` to be told after every change. Every location transition is also
    fired on the bus as :data:`~.const.EVENT_TRANSITION`.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """:param hass: Home Assistant.
        :param entry: the room_presence config entry (data, options and place subentries).
        """
        self.hass = hass
        self.entry = entry
        conf = {**entry.data, **entry.options}
        self.cfg = engine_config(conf)
        self.places = entry_places(entry)
        self.lookback = timedelta(
            hours=float(conf.get(CONF_REBUILD_LOOKBACK, DEFAULTS[CONF_REBUILD_LOOKBACK]))
        )
        self.machine = PresenceMachine(self.cfg, self.places)
        self.transitions: deque[Transition] = deque(maxlen=200)
        self.edges: deque[StateEvent] = deque(maxlen=500)
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._timer: CALLBACK_TYPE | None = None
        self._delayed: CALLBACK_TYPE | None = None
        self._store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}.manual"
        )
        self._manual: list[dict[str, str]] = []

    @property
    def snapshot(self) -> Snapshot:
        """The engine's current answer."""
        return self.machine.snapshot()

    @property
    def inputs(self) -> list[str]:
        """Every configured input entity id."""
        c = self.cfg
        ids = [c.fp2_room, c.fp2_bed, c.fp2_desk, c.bathroom, c.movement]
        ids += [*c.door_witnesses, *c.fallback_motion, *c.sleep_entities]
        ids += [c.phone_tracker, c.phone_ssid]
        return list(dict.fromkeys(i for i in ids if i))

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Call ``cb`` after every input or deadline; returns the function that removes it."""
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    # -- lifecycle ------------------------------------------------------------------------------

    async def async_start(self) -> None:
        """Rebuild the state from recorder history and stored corrections, then follow live.

        History of every input over the lookback (24 h by default) and the stored ``set_state``
        calls in that window are replayed into the fresh engine; their transitions are logged but
        not fired on the bus. Stored calls older than the window are dropped. Without the
        recorder, only the current states are used.
        """
        now = dt_util.utcnow()
        start = now - self.lookback
        stored = await self._store.async_load() or {}
        calls = list(stored.get("calls", []))
        self._manual = [m for m in calls if datetime.fromisoformat(m["at"]) >= start]
        if len(self._manual) != len(calls):
            await self._store.async_save({"calls": self._manual})
        events: list[StateEvent | ManualEvent] = []
        if "recorder" in self.hass.config.components:
            try:
                events += await self._history(start)
            except Exception:  # noqa: BLE001 - any recorder failure: start without history
                _LOGGER.warning(
                    "Could not read recorder history; starting from current states only",
                    exc_info=True,
                )
        self.edges.extend(e for e in events if isinstance(e, StateEvent))
        events += [ManualEvent(datetime.fromisoformat(m["at"]), m["state"]) for m in self._manual]
        events.sort(key=lambda e: e.at)
        self.transitions.extend(replay(self.machine, events, until=now))
        self._feed_current_states()
        self._unsubs.append(
            async_track_state_change_event(self.hass, self.inputs, self._on_state_change)
        )
        self._schedule()

    async def async_stop(self) -> None:
        """Stop listening and cancel every timer."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for name in ("_timer", "_delayed"):
            cancel = getattr(self, name)
            if cancel is not None:
                cancel()
                setattr(self, name, None)

    # -- set_state ------------------------------------------------------------------------------

    def validate_state(self, state: str) -> None:
        """Raise :class:`ValueError` unless ``state`` is a valid ``set_state`` target."""
        self.machine.check_state(state)

    async def async_set_state(self, state: str, delay: float | None = None) -> None:
        """Force ``state`` now, or after ``delay`` seconds (replacing a pending delayed call).

        :raises ValueError: ``state`` is not a fixed location (except ``unknown``) or place slug.
        """
        self.validate_state(state)
        if self._delayed is not None:
            self._delayed()
            self._delayed = None
        if delay:

            @callback
            def _later(_now: datetime) -> None:
                self._delayed = None
                self.hass.async_create_task(self._apply_manual(state))

            self._delayed = async_call_later(self.hass, delay, _later)
            return
        await self._apply_manual(state)

    async def _apply_manual(self, state: str) -> None:
        now = dt_util.utcnow()
        self._publish(self.machine.feed(ManualEvent(now, state)))
        self._manual.append({"at": now.isoformat(), "state": state})
        cutoff = now - self.lookback
        self._manual = [m for m in self._manual if datetime.fromisoformat(m["at"]) >= cutoff]
        await self._store.async_save({"calls": self._manual})
        self._schedule()

    # -- internals ------------------------------------------------------------------------------

    async def _history(self, start: datetime) -> list[StateEvent]:
        """Return recorder history of every input since ``start`` as engine events."""
        from homeassistant.components.recorder import get_instance, history  # noqa: PLC0415

        def read() -> dict[str, list[Any]]:
            return history.get_significant_states(
                self.hass,
                start,
                None,
                self.inputs,
                include_start_time_state=True,
                significant_changes_only=False,
                no_attributes=True,
            )

        states = await get_instance(self.hass).async_add_executor_job(read)
        rows = {eid: [(st.last_changed, st.state) for st in sts] for eid, sts in states.items()}
        return [self._event(e.entity_id, e.state, e.at) for e in history_events(rows)]

    def _zone_entity(self, state: str) -> str:
        """Map a tracker state (a zone *name*) to the zone entity id the places use."""
        if state == "home" or state.startswith("zone."):
            return state
        for zone in self.hass.states.async_all("zone"):
            if state in (zone.name, zone.attributes.get("friendly_name")):
                return zone.entity_id
        return state

    def _event(self, entity_id: str, state: str, at: datetime) -> StateEvent:
        if entity_id == self.cfg.phone_tracker:
            state = self._zone_entity(state)
        return StateEvent(at, entity_id, state)

    def _feed_current_states(self) -> None:
        current = [s for e in self.inputs if (s := self.hass.states.get(e)) is not None]
        for st in sorted(current, key=lambda s: s.last_changed):
            self._feed(self._event(st.entity_id, st.state, st.last_changed))

    @callback
    def _on_state_change(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        state = new.state if new is not None else "unavailable"
        at = new.last_changed if new is not None else dt_util.utcnow()
        self._feed(self._event(event.data["entity_id"], state, at))
        self._schedule()

    def _feed(self, ev: StateEvent) -> None:
        self.edges.append(ev)
        self._publish(self.machine.feed(ev))

    def _publish(self, transitions: list[Transition]) -> None:
        for tr in transitions:
            self.transitions.append(tr)
            self.hass.bus.async_fire(
                EVENT_TRANSITION,
                {
                    "entry_id": self.entry.entry_id,
                    "old": tr.old,
                    "new": tr.new,
                    "reason": tr.reason,
                    "at": tr.at.isoformat(),
                },
            )
        for cb in list(self._listeners):
            cb()

    def _schedule(self) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None
        deadline = self.machine.next_deadline()
        if deadline is not None:
            self._timer = async_track_point_in_utc_time(self.hass, self._on_deadline, deadline)

    @callback
    def _on_deadline(self, now: datetime) -> None:
        self._timer = None
        self._publish(self.machine.tick(now))
        self._schedule()
