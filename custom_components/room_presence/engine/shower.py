"""Showering in one bathroom, from a sharp rise of its humidity sensor while someone is there.

The humidity sensor reports rarely while humidity is steady and on every small step while it
changes, so the rule works on change-triggered readings: a reading at least ``shower_rise`` points
above the lowest reading of the last ``shower_window`` starts a shower, and the shower ends
``shower_end`` after the last new high.
"""

from __future__ import annotations

from datetime import datetime

from .rooms import RoomSensors
from .types import RoomConfig, StateEvent, Timings, Transition


class ShowerTracker:
    """Whether the person is showering in one bathroom room (spec §5.5).

    Transitions use ``"dry"``/``"showering"`` as ``old``/``new``. Reasons: ``shower_start``,
    ``shower_end``, or the reason given to :meth:`end` (``exit``, ``manual``).
    """

    def __init__(self, room: RoomConfig, timings: Timings) -> None:
        """:param room: a bathroom room with a ``humidity`` sensor.
        :param timings: ``shower_rise``, ``shower_window`` and ``shower_end``.
        """
        self.room = room
        self.timings = timings
        self._readings: list[tuple[datetime, float]] = []
        self._showering = False
        self._since: datetime | None = None
        self._high = 0.0
        self._high_at: datetime | None = None

    @property
    def showering(self) -> bool:
        """True while showering."""
        return self._showering

    @property
    def since(self) -> datetime | None:
        """When the current shower began (back-dated), else ``None``."""
        return self._since if self._showering else None

    def feed(self, ev: StateEvent, sensors: RoomSensors, allowed: bool = True) -> list[Transition]:
        """Apply one event; only numeric readings of the room's humidity sensor count.

        :param sensors: this room's sensors, already fed ``ev``, for the presence-edge condition.
        :param allowed: whether a shower may start now (``False`` while the person is out: the
            readings are still kept, but a rise is someone else's).
        """
        value = self.observe(ev)
        out = self.tick(ev.at)
        if value is None:
            return out
        at, window = ev.at, self.timings.shower_window
        self._readings.append((at, value))
        self._readings = [(t, v) for t, v in self._readings if t >= at - window]
        if self._showering or not allowed:
            return out
        low = min(v for _, v in self._readings)
        if value - low < self.timings.shower_rise:
            return out
        presence = sensors.first_edge(("presence",), at - window, at)
        if presence is None:
            return out
        low_at = max(t for t, v in self._readings if v == low)
        since = max(low_at, presence)
        self._showering, self._since = True, since
        self._high, self._high_at = value, at
        return [*out, Transition(since, "dry", "showering", "shower_start")]

    def observe(self, ev: StateEvent) -> float | None:
        """Note a new high from ``ev`` before any deadline runs, so that a new high arriving
        exactly at the end deadline keeps the shower going; return the reading, or ``None``."""
        value = self._value(ev)
        if value is not None and self._showering and value > self._high:
            self._high, self._high_at = value, ev.at
        return value

    def _value(self, ev: StateEvent) -> float | None:
        if ev.entity_id != self.room.humidity:
            return None
        try:
            return float(ev.state)
        except ValueError:
            return None

    def end(self, at: datetime, reason: str) -> list[Transition]:
        """End the shower at ``at`` (exit crossing, ``set_state``); nothing if not showering."""
        if not self._showering:
            return []
        self._showering = False
        self._high_at = None
        # Humidity stays high for a while after a shower; only readings from now on may form the
        # low point a new shower rises from.
        self._readings = [(t, v) for t, v in self._readings if t >= at]
        return [Transition(at, "showering", "dry", reason)]

    def next_deadline(self) -> datetime | None:
        """When the shower ends if humidity sets no new high, or ``None``."""
        if not self._showering or self._high_at is None:
            return None
        return self._high_at + self.timings.shower_end

    def tick(self, now: datetime) -> list[Transition]:
        """End the shower if ``shower_end`` has passed since the last new high."""
        deadline = self.next_deadline()
        if deadline is None or now < deadline:
            return []
        return self.end(deadline, "shower_end")
