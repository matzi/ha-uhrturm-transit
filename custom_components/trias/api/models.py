"""Data models for TRIAS responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def same_stop(ref: str | None, target: str | None) -> bool:
    """Return True if both refs denote the same stop.

    Works across hierarchy levels: platform ``at:42:1234:0:2`` matches
    station ``at:42:1234`` and vice versa.
    """
    if not ref or not target:
        return False
    return ref == target or ref.startswith(target + ":") or target.startswith(ref + ":")


@dataclass(slots=True)
class StopInfo:
    """A stop found by a location search."""

    ref: str
    name: str | None
    locality: str | None
    kind: str
    latitude: float | None = None
    longitude: float | None = None

    @property
    def label(self) -> str:
        """Human readable label."""
        name = self.name or self.ref
        if self.locality and self.locality not in name:
            name = f"{name} ({self.locality})"
        return name

    def as_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "ref": self.ref,
            "name": self.name,
            "locality": self.locality,
            "kind": self.kind,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }


@dataclass(slots=True)
class Call:
    """A vehicle call at a stop."""

    stop_ref: str | None
    stop_name: str | None
    bay: str | None
    timetabled: datetime | None
    estimated: datetime | None

    @property
    def time(self) -> datetime | None:
        """Best known time (real-time if available)."""
        return self.estimated or self.timetabled

    @property
    def delay_min(self) -> int | None:
        """Delay in minutes, None without real-time data."""
        if self.estimated and self.timetabled:
            return round((self.estimated - self.timetabled).total_seconds() / 60)
        return None


@dataclass(slots=True)
class ServiceInfo:
    """Service (line/journey) information."""

    line: str | None
    destination: str | None
    mode: str | None
    line_ref: str | None
    direction_ref: str | None
    journey_ref: str | None
    cancelled: bool = False


@dataclass(slots=True)
class Departure:
    """A departure at a stop."""

    service: ServiceInfo
    call: Call
    onward: list[Call] = field(default_factory=list)

    def passes(self, target_ref: str | None = None, target_name: str | None = None) -> bool:
        """Return True if the vehicle stops at the target later on."""
        if target_ref:
            return any(same_stop(c.stop_ref, target_ref) for c in self.onward)
        if target_name:
            needle = target_name.casefold()
            return any(c.stop_name and needle in c.stop_name.casefold() for c in self.onward)
        return True

    def as_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "line": self.service.line,
            "destination": self.service.destination,
            "mode": self.service.mode,
            "stop": self.call.stop_name,
            "planned": _iso(self.call.timetabled),
            "expected": _iso(self.call.estimated),
            "time": _iso(self.call.time),
            "delay": self.call.delay_min,
            "realtime": self.call.estimated is not None,
            "platform": self.call.bay,
            "cancelled": self.service.cancelled,
            "line_ref": self.service.line_ref,
            "direction_ref": self.service.direction_ref,
        }


@dataclass(slots=True)
class Leg:
    """A trip leg."""

    kind: str  # "pt" | "walk" | "interchange"
    service: ServiceInfo | None = None
    board: Call | None = None
    alight: Call | None = None
    start_name: str | None = None
    end_name: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration: timedelta | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialize."""
        if self.kind == "pt" and self.service and self.board and self.alight:
            return {
                "type": "pt",
                "line": self.service.line,
                "destination": self.service.destination,
                "mode": self.service.mode,
                "from": self.board.stop_name,
                "platform": self.board.bay,
                "departure_planned": _iso(self.board.timetabled),
                "departure": _iso(self.board.time),
                "departure_delay": self.board.delay_min,
                "to": self.alight.stop_name,
                "arrival_planned": _iso(self.alight.timetabled),
                "arrival": _iso(self.alight.time),
                "cancelled": self.service.cancelled,
            }
        return {
            "type": self.kind,
            "from": self.start_name,
            "to": self.end_name,
            "minutes": round(self.duration.total_seconds() / 60) if self.duration else None,
        }


@dataclass(slots=True)
class Trip:
    """A trip (connection) from a TripRequest."""

    start: datetime | None
    end: datetime | None
    duration: timedelta | None
    interchanges: int
    legs: list[Leg]

    @property
    def pt_legs(self) -> list[Leg]:
        """Public transport legs only."""
        return [leg for leg in self.legs if leg.kind == "pt"]

    @property
    def cancelled(self) -> bool:
        """True if any leg is cancelled."""
        return any(leg.service and leg.service.cancelled for leg in self.pt_legs)

    def as_dict(self) -> dict[str, Any]:
        """Serialize."""
        pt = self.pt_legs
        return {
            "start": _iso(self.start),
            "end": _iso(self.end),
            "departure": _iso(pt[0].board.time) if pt and pt[0].board else None,
            "arrival": _iso(pt[-1].alight.time) if pt and pt[-1].alight else None,
            "duration_min": round(self.duration.total_seconds() / 60) if self.duration else None,
            "changes": self.interchanges,
            "lines": [leg.service.line for leg in pt if leg.service],
            "cancelled": self.cancelled,
            "legs": [leg.as_dict() for leg in self.legs],
        }


def filter_departures(
    departures: list[Departure],
    *,
    towards_ref: str | None = None,
    towards_name: str | None = None,
    lines: list[str] | None = None,
    direction_ref: str | None = None,
) -> list[Departure]:
    """Filter departures client-side."""
    wanted_lines = {line.casefold() for line in lines or [] if line}
    result = []
    for dep in departures:
        if wanted_lines and (dep.service.line or "").casefold() not in wanted_lines:
            continue
        if (
            direction_ref
            and (dep.service.direction_ref or "").casefold() != direction_ref.casefold()
        ):
            continue
        if (towards_ref or towards_name) and not dep.passes(towards_ref, towards_name):
            continue
        result.append(dep)
    return result
