"""Commute planning: pick the best of several home stations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from .client import TriasClient
from .exceptions import TriasApiError, TriasError
from .models import Trip, same_stop

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class Place:
    """A configured station with walking time."""

    ref: str
    name: str
    walk_min: int


@dataclass(slots=True)
class CommuteOption:
    """One way to get to work."""

    leave: datetime  # leave home at
    station: str  # configured station to walk to
    board_stop: str | None  # stop where the first vehicle is boarded
    departure: datetime  # departure of the first vehicle
    arrive: datetime  # arrival at work incl. walk from the work stop
    trip: Trip

    @property
    def delay_min(self) -> int | None:
        """Delay of the first vehicle."""
        first = self.trip.pt_legs[0]
        return first.board.delay_min if first.board else None

    @property
    def max_delay_min(self) -> int:
        """Largest known delay over all legs (departures and arrivals)."""
        delays = [0]
        for leg in self.trip.pt_legs:
            for call in (leg.board, leg.alight):
                if call and call.delay_min is not None:
                    delays.append(call.delay_min)
        return max(delays)

    def as_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "leave": self.leave.isoformat(),
            "station": self.station,
            "board_stop": self.board_stop,
            "departure": self.departure.isoformat(),
            "delay": self.delay_min,
            "arrival": self.arrive.isoformat(),
            "duration_min": round((self.arrive - self.leave).total_seconds() / 60),
            "changes": self.trip.interchanges,
            "lines": [leg.service.line for leg in self.trip.pt_legs if leg.service],
            "cancelled": self.trip.cancelled,
            "legs": [leg.as_dict() for leg in self.trip.legs],
        }


def make_option(
    trip: Trip, origin: Place, work: Place, stations: list[Place]
) -> CommuteOption | None:
    """Turn a trip into a commute option (None if unusable)."""
    pt = trip.pt_legs
    if not pt:
        return None
    first, last = pt[0], pt[-1]
    if not first.board or not last.alight:
        return None
    dep, arr = first.board.time, last.alight.time
    if dep is None or arr is None:
        return None

    # The planner may walk from the requested station to a neighbouring one:
    # attribute the option to the station that is actually boarded.
    boarded = next((s for s in stations if same_stop(first.board.stop_ref, s.ref)), None)
    if boarded is not None:
        leave = dep - timedelta(minutes=boarded.walk_min)
        station = boarded.name
    else:
        leave = (trip.start or dep) - timedelta(minutes=origin.walk_min)
        station = origin.name

    idx = max(i for i, leg in enumerate(trip.legs) if leg.kind == "pt")
    trailing = sum((leg.duration or timedelta() for leg in trip.legs[idx + 1 :]), timedelta())
    arrive = arr + trailing + timedelta(minutes=work.walk_min)
    return CommuteOption(
        leave=leave,
        station=station,
        board_stop=first.board.stop_name,
        departure=dep,
        arrive=arrive,
        trip=trip,
    )


def dedupe(options: list[CommuteOption]) -> list[CommuteOption]:
    """Merge identical connections found from different stations."""
    best: dict[tuple, CommuteOption] = {}
    for opt in options:
        first, last = opt.trip.pt_legs[0], opt.trip.pt_legs[-1]
        key = (
            first.service.line if first.service else None,
            first.board.timetabled if first.board else None,
            last.service.line if last.service else None,
            last.alight.timetabled if last.alight else None,
        )
        cur = best.get(key)
        if cur is None or opt.leave > cur.leave:
            best[key] = opt
    return list(best.values())


def pareto(options: list[CommuteOption]) -> list[CommuteOption]:
    """Drop options where another leaves later (or same) and arrives earlier (or same)."""
    ordered = sorted(options, key=lambda o: (-o.leave.timestamp(), o.arrive, o.trip.interchanges))
    result: list[CommuteOption] = []
    best_arrive: datetime | None = None
    for opt in ordered:
        if best_arrive is None or opt.arrive < best_arrive:
            result.append(opt)
            best_arrive = opt.arrive
    return result


async def plan_commute(
    client: TriasClient,
    stations: list[Place],
    work: Place,
    now: datetime,
    *,
    per_station: int = 6,
    keep_dominated: bool = False,
) -> list[CommuteOption]:
    """Return commute options sorted by leave time.

    Raises the first error only if every station query failed.
    """

    async def query(station: Place) -> list[Trip]:
        return await client.trips(
            station.ref, work.ref, now + timedelta(minutes=station.walk_min), per_station
        )

    results = await asyncio.gather(*(query(s) for s in stations), return_exceptions=True)

    options: list[CommuteOption] = []
    horizons: list[datetime] = []
    errors: list[BaseException] = []
    for station, res in zip(stations, results, strict=True):
        if isinstance(res, TriasApiError):
            _LOGGER.debug("No trips from %s: %s", station.name, res)
            continue
        if isinstance(res, BaseException):
            if not isinstance(res, TriasError):
                raise res
            errors.append(res)
            continue
        got = [o for t in res if (o := make_option(t, station, work, stations))]
        # a query that hit the result limit may have hidden later options
        if got and len(res) >= per_station:
            horizons.append(max(o.leave for o in got))
        options.extend(got)

    if errors and len(errors) == len(stations):
        raise errors[0]

    options = [o for o in options if o.leave >= now - timedelta(minutes=1)]
    if horizons:
        horizon = min(horizons)
        options = [o for o in options if o.leave <= horizon]
    options = dedupe(options)
    if not keep_dominated:
        options = pareto(options)
    return sorted(options, key=lambda o: (o.leave, o.arrive))
