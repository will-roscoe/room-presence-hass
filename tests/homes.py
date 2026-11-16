"""Home configurations shared by the tests, built from Will's live entity ids."""

from datetime import timedelta

from custom_components.room_presence.engine.types import (
    HomeConfig,
    RoomConfig,
    SpotConfig,
    Timings,
)

FP2 = "binary_sensor.aqara_fp2_{}_presence"
THP = "binary_sensor.tuya_thp_{}_presence"
HUM = "sensor.tuya_th_4_humidity"


def bedroom(
    *,
    bed: bool = True,
    desk: bool = True,
    exits: tuple[str, ...] = (FP2.format("door"), THP.format(3)),
    motion: tuple[str, ...] = (),
    stillness: str | None = THP.format(1),
    sleep_entities: tuple[str, ...] = (),
) -> RoomConfig:
    """The bedroom: presence from the FP2 room zone, spots bed (role bed) then desk."""
    spots = []
    if bed:
        spots.append(
            SpotConfig(
                "bed",
                "Bed",
                FP2.format("bed"),
                role="bed",
                stillness=stillness,
                sleep_entities=sleep_entities,
            )
        )
    if desk:
        spots.append(SpotConfig("desk", "Desk", FP2.format("desk")))
    return RoomConfig(
        "bedroom",
        "Bedroom",
        presence=(FP2.format("room"),),
        motion=motion,
        exits=exits,
        spots=tuple(spots),
    )


def bathroom(*, exclusive: bool = True, humidity: str | None = HUM) -> RoomConfig:
    """The ensuite: presence from THP 2, a bathroom with the secondary humidity sensor."""
    return RoomConfig(
        "bathroom",
        "Bathroom",
        presence=(THP.format(2),),
        bathroom=True,
        exclusive=exclusive,
        humidity=humidity,
    )


def home(*rooms: RoomConfig, person: str | None = "person.p", **overrides) -> HomeConfig:
    """A home of ``rooms``; ``overrides`` are Timings fields (seconds for durations) or HomeConfig
    fields (``home_zone``, ``home_ssids``, ``ssid_sensors``, ``zone_names``)."""
    timing_names = set(Timings.__dataclass_fields__)
    timings = {
        k: (
            timedelta(seconds=v)
            if isinstance(v, int | float) and not isinstance(v, bool) and k != "shower_rise"
            else v
        )
        for k, v in overrides.items()
        if k in timing_names
    }
    rest = {k: v for k, v in overrides.items() if k not in timing_names}
    return HomeConfig(rooms=tuple(rooms), person=person, timings=Timings(**timings), **rest)


#: The configuration the v0.1 migration produces for Will's home.
LIVE = home(bedroom(motion=(THP.format(1), THP.format(3), THP.format(4))), bathroom())
