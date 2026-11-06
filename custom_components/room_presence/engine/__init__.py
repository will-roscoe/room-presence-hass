"""Pure presence engine: every room_presence rule, with no Home Assistant imports.

Feed it :class:`~.types.StateEvent` and :class:`~.types.ManualEvent` objects in time order and read
the transitions it returns and its snapshot.
"""

from .types import EngineConfig, ManualEvent, RoomState, Snapshot, StateEvent, Sub, Transition

__all__ = [
    "EngineConfig",
    "ManualEvent",
    "RoomState",
    "Snapshot",
    "StateEvent",
    "Sub",
    "Transition",
]
