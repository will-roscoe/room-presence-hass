"""Replay recorded history through a :class:`~.machine.PresenceMachine`.

The replay tests and the startup rebuild both use these two functions:

.. code-block:: python

    events = history_events({"binary_sensor.fp2_room": [["2000-01-03T18:40:35+00:00", "on"]]})
    transitions = replay(PresenceMachine(cfg, places), events, until=now)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from .machine import PresenceMachine
from .types import ManualEvent, StateEvent, Transition


def _parse(when: str | datetime) -> datetime:
    return when if isinstance(when, datetime) else datetime.fromisoformat(when)


def history_events(
    history: Mapping[str, Sequence[Sequence[str | datetime]]],
) -> list[StateEvent]:
    """Return every ``[time, state]`` row of ``history`` as a :class:`~.types.StateEvent`.

    :param history: entity id → rows of ``[time, state]``; times are ISO strings or aware
        datetimes.
    :returns: events sorted by time; equal times keep their order in ``history`` (entity order,
        then row order).
    """
    rows = [
        StateEvent(_parse(when), entity_id, str(state))
        for entity_id, entity_rows in history.items()
        for when, state in entity_rows
    ]
    return sorted(rows, key=lambda ev: ev.at)


def replay(
    machine: PresenceMachine,
    events: Iterable[StateEvent | ManualEvent],
    until: datetime | None = None,
) -> list[Transition]:
    """Feed ``events`` (time order) into ``machine`` and return its location transitions.

    Deadlines between events run as the machine is fed; with ``until``, deadlines up to that time
    run after the last event. A :class:`~.types.ManualEvent` the machine rejects is skipped.
    """
    out: list[Transition] = []
    for ev in events:
        try:
            out += machine.feed(ev)
        except ValueError:
            continue
    if until is not None:
        out += machine.tick(until)
    return out
