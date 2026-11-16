"""Pure presence engine: every room_presence rule, with no Home Assistant imports.

Build a :class:`HomeMachine` from a :class:`HomeConfig` and the places, feed it
:class:`StateEvent` and :class:`ManualEvent` objects in time order (or use :func:`replay`), and read
the transitions it returns and its :class:`Snapshot`.
"""

from .home import HomeMachine
from .places import Place
from .types import (
    Activity,
    HomeConfig,
    ManualEvent,
    Presence,
    RoomConfig,
    Snapshot,
    SpotConfig,
    StateEvent,
    Timings,
    Transition,
)

__all__ = [
    "Activity",
    "HomeConfig",
    "HomeMachine",
    "ManualEvent",
    "Place",
    "Presence",
    "RoomConfig",
    "Snapshot",
    "SpotConfig",
    "StateEvent",
    "Timings",
    "Transition",
]
