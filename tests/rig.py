"""Test rig: RoomSensors and a PersonTracker driven together, as HomeMachine routes events."""

from datetime import UTC, datetime, timedelta

from custom_components.room_presence.engine.person import PersonTracker
from custom_components.room_presence.engine.rooms import RoomSensors
from custom_components.room_presence.engine.types import HomeConfig, StateEvent, Transition

T0 = datetime(2000, 2, 3, 12, 0, tzinfo=UTC)


def t(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


class Rig:
    """Feed entity changes at second offsets from :data:`T0`; transitions collect in :attr:`log`."""

    def __init__(self, home: HomeConfig) -> None:
        self.home = home
        self.rooms = {r.slug: RoomSensors(r, home.timings) for r in home.rooms}
        self.person = PersonTracker(home, self.rooms)
        self.log: list[Transition] = []

    def tick(self, seconds: float) -> list[Transition]:
        out = self.person.tick(t(seconds))
        self.log += out
        return out

    def feed(self, seconds: float, entity: str, state: str) -> list[Transition]:
        out = self.person.tick(t(seconds))
        for slug, _ in self.home.owners(entity):
            ch = self.rooms[slug].feed(StateEvent(t(seconds), entity, state))
            if ch is not None:
                out += self.person.on_change(slug, ch)
        self.log += out
        return out

    def run(self, *events: tuple[float, str, str]) -> list[Transition]:
        out = []
        for e in events:
            out += self.feed(*e)
        return out

    @property
    def view(self) -> str:
        p = self.person
        if p.room is None:
            return p.presence.value
        return p.room if p.spot is None else f"{p.room}/{p.spot}"
