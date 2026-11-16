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
    CONF_ENTRY_NEEDS_CROSSING,
    CONF_EXCLUSIVE,
    CONF_EXITS,
    CONF_HOME_SSIDS,
    CONF_HOME_ZONE,
    CONF_HUMIDITY,
    CONF_MODE,
    CONF_MOTION,
    CONF_NAME,
    CONF_PERSON,
    CONF_PRESENCE,
    CONF_PRIORITY,
    CONF_REBUILD_LOOKBACK,
    CONF_ROLE,
    CONF_SENSOR,
    CONF_SHOWER_RISE,
    CONF_SLEEP_ENTITIES,
    CONF_SPOTS,
    CONF_SSID_SENSORS,
    CONF_SSIDS,
    CONF_STILLNESS,
    CONF_ZONES,
    DEFAULTS,
    DOMAIN,
    EVENT_TRANSITION,
    SUBENTRY_PERSON,
    SUBENTRY_PLACE,
    SUBENTRY_ROOM,
    TIMING_KEYS,
)
from .engine import (
    HomeConfig,
    HomeMachine,
    ManualEvent,
    Place,
    RoomConfig,
    Snapshot,
    SpotConfig,
    StateEvent,
    Timings,
    Transition,
    history_events,
    replay,
)

_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1
#: Person states that are not zone names.
_NOT_ZONES = frozenset({"home", "not_home", "unavailable", "unknown"})


def timings(conf: Mapping[str, Any]) -> Timings:
    """Build :class:`~.engine.types.Timings` from entry options (seconds, points, a flag)."""
    durations = {k: timedelta(seconds=float(conf.get(k, DEFAULTS[k]))) for k in TIMING_KEYS}
    return Timings(
        **durations,
        shower_rise=float(conf.get(CONF_SHOWER_RISE, DEFAULTS[CONF_SHOWER_RISE])),
        entry_needs_crossing=bool(
            conf.get(CONF_ENTRY_NEEDS_CROSSING, DEFAULTS[CONF_ENTRY_NEEDS_CROSSING])
        ),
    )


def room_config(data: Mapping[str, Any]) -> RoomConfig:
    """Build a :class:`~.engine.types.RoomConfig` from a room subentry's data (slug = slugified
    name; spot slugs likewise)."""
    spots = tuple(
        SpotConfig(
            slug=slugify(s[CONF_NAME]),
            name=s[CONF_NAME],
            sensor=s[CONF_SENSOR],
            role=s.get(CONF_ROLE) or None,
            stillness=s.get(CONF_STILLNESS) or None,
            sleep_entities=tuple(s.get(CONF_SLEEP_ENTITIES) or ()),
        )
        for s in data.get(CONF_SPOTS) or ()
    )
    bathroom = bool(data.get(CONF_BATHROOM, False))
    return RoomConfig(
        slug=slugify(data[CONF_NAME]),
        name=data[CONF_NAME],
        presence=tuple(data.get(CONF_PRESENCE) or ()),
        motion=tuple(data.get(CONF_MOTION) or ()),
        exits=tuple(data.get(CONF_EXITS) or ()),
        spots=spots,
        bathroom=bathroom,
        exclusive=bool(data.get(CONF_EXCLUSIVE, False)),
        humidity=(data.get(CONF_HUMIDITY) or None) if bathroom else None,
    )


def person_subentry(entry: ConfigEntry) -> Any | None:
    """Return the entry's person subentry, or ``None`` if it has none."""
    return next((s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_PERSON), None)


def home_config(hass: HomeAssistant, entry: ConfigEntry) -> HomeConfig:
    """Build the engine's :class:`~.engine.types.HomeConfig` from the entry and its subentries.

    Rooms come in subentry creation order; zone names are read from the current ``zone`` states.

    :raises ValueError: the rooms do not form a valid home (see :class:`HomeConfig`).
    """
    conf = {**entry.data, **entry.options}
    rooms = tuple(
        room_config(s.data) for s in entry.subentries.values() if s.subentry_type == SUBENTRY_ROOM
    )
    person = person_subentry(entry)
    return HomeConfig(
        rooms=rooms,
        person=person.data.get(CONF_PERSON) if person else None,
        home_zone=conf.get(CONF_HOME_ZONE) or "zone.home",
        home_ssids=frozenset(conf.get(CONF_HOME_SSIDS) or ()),
        ssid_sensors=tuple(person.data.get(CONF_SSID_SENSORS) or ()) if person else (),
        zone_names={z.entity_id: z.name for z in hass.states.async_all("zone")},
        timings=timings(conf),
    )


def entry_places(entry: ConfigEntry) -> list[Place]:
    """Return the entry's place subentries as :class:`~.engine.places.Place` objects."""
    return [
        Place(
            slug=slugify(s.data[CONF_NAME]),
            name=s.data[CONF_NAME],
            ssids=tuple(s.data.get(CONF_SSIDS) or ()),
            zones=tuple(s.data.get(CONF_ZONES) or ()),
            mode=s.data.get(CONF_MODE, "any"),
            priority=int(s.data.get(CONF_PRIORITY, 0)),
        )
        for s in entry.subentries.values()
        if s.subentry_type == SUBENTRY_PLACE
    ]


class PresenceCoordinator:
    """Runs the home's :class:`~.engine.home.HomeMachine` against live Home Assistant state.

    Read :attr:`snapshot` for the answer, :attr:`transitions` and :attr:`edges` for the recent
    log; :meth:`add_listener` to be told after every change. Every status change is also fired
    on the bus as :data:`~.const.EVENT_TRANSITION`.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """:param hass: Home Assistant.
        :param entry: the room_presence entry (data, options, person, room and place subentries).
        :raises ValueError: the rooms do not form a valid home.
        """
        self.hass = hass
        self.entry = entry
        conf = {**entry.data, **entry.options}
        self.home = home_config(hass, entry)
        self.places = entry_places(entry)
        self.lookback = timedelta(
            hours=float(conf.get(CONF_REBUILD_LOOKBACK, DEFAULTS[CONF_REBUILD_LOOKBACK]))
        )
        self.machine = HomeMachine(self.home, self.places)
        self.transitions: deque[Transition] = deque(maxlen=200)
        self.edges: deque[StateEvent] = deque(maxlen=500)
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._timer: CALLBACK_TYPE | None = None
        self._delayed: CALLBACK_TYPE | None = None
        self._store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}.manual_v2"
        )
        self._manual: list[dict[str, str | None]] = []

    @property
    def snapshot(self) -> Snapshot:
        """The engine's current answer."""
        return self.machine.snapshot()

    @property
    def inputs(self) -> list[str]:
        """Every input entity id."""
        return list(self.home.inputs())

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Call ``cb`` after every input or deadline; returns the function that removes it."""
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    # -- lifecycle ------------------------------------------------------------------------------

    async def async_start(self) -> None:
        """Rebuild the state from recorder history and stored corrections, then follow live.

        History of every input over the look-back (24 h by default) and the stored ``set_state``
        calls in that window are replayed into the fresh engine; their transitions are logged but
        not fired on the bus. Stored calls older than the window are dropped, and calls the
        current configuration rejects (a deleted room, say) are skipped. Without the recorder,
        or if reading it fails, only the current states are used.
        """
        now = dt_util.utcnow()
        start = now - self.lookback
        stored = await self._store.async_load() or {}
        calls = list(stored.get("calls", []))
        self._manual = [m for m in calls if datetime.fromisoformat(str(m["at"])) >= start]
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
        events += [
            ManualEvent(datetime.fromisoformat(str(m["at"])), m.get("location"), m.get("activity"))
            for m in self._manual
        ]
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

    async def async_set_state(
        self, location: str | None, activity: str | None, delay: float | None = None
    ) -> None:
        """Force ``location`` and/or ``activity`` now, or after ``delay`` seconds (replacing a
        pending delayed call).

        :raises ValueError: the engine rejects the call (see
            :meth:`~.engine.home.HomeMachine.check_manual`).
        """
        self.machine.check_manual(location, activity)
        if self._delayed is not None:
            self._delayed()
            self._delayed = None
        if delay:

            @callback
            def _later(_now: datetime) -> None:
                self._delayed = None
                self.hass.async_create_task(self._apply_manual(location, activity))

            self._delayed = async_call_later(self.hass, delay, _later)
            return
        await self._apply_manual(location, activity)

    async def _apply_manual(self, location: str | None, activity: str | None) -> None:
        now = dt_util.utcnow()
        self._publish(self.machine.feed(ManualEvent(now, location, activity)))
        self._manual.append({"at": now.isoformat(), "location": location, "activity": activity})
        cutoff = now - self.lookback
        self._manual = [m for m in self._manual if datetime.fromisoformat(str(m["at"])) >= cutoff]
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
        """Map a person state that is a zone *name* to the zone entity id the engine uses."""
        if state in _NOT_ZONES or state.startswith("zone."):
            return state
        for zone in self.hass.states.async_all("zone"):
            if state in (zone.name, zone.attributes.get("friendly_name")):
                return zone.entity_id
        return state

    def _event(self, entity_id: str, state: str, at: datetime) -> StateEvent:
        if entity_id == self.home.person:
            state = self._zone_entity(state)
        return StateEvent(at, entity_id, state)

    def _feed_current_states(self) -> None:
        current = [s for e in self.inputs if (s := self.hass.states.get(e)) is not None]
        for st in sorted(current, key=lambda s: s.last_changed):
            self._feed(self._event(st.entity_id, st.state, st.last_changed))

    @callback
    def _on_state_change(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if old is not None and new is not None and old.state == new.state:
            return  # an attribute-only update says nothing new
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
