"""Tests for the API layer (parser, filters, commute logic)."""

from __future__ import annotations

from datetime import datetime, timedelta, UTC

import pytest

from custom_components.uhrturm_oepnv.api import parser
from custom_components.uhrturm_oepnv.api.commute import Place, dedupe, make_option, pareto
from custom_components.uhrturm_oepnv.api.exceptions import TriasApiError, TriasParseError
from custom_components.uhrturm_oepnv.api.models import filter_departures, same_stop

from .conftest import fixture

BASE = datetime(2026, 11, 16, 5, 30, tzinfo=UTC)


def _root(name: str, tag: str):
    root, _ = parser.parse_root(fixture(name).encode(), tag)
    return root


def test_same_stop() -> None:
    """Station and platform IDs match each other."""
    assert same_stop("at:46:2000:0:1", "at:46:2000")
    assert same_stop("at:46:2000", "at:46:2000:0:1")
    assert not same_stop("at:46:20001", "at:46:2000")
    assert not same_stop(None, "at:46:2000")


def test_parse_dt_and_duration() -> None:
    """Datetime and duration parsing."""
    assert parser.parse_dt("2026-11-16T05:30:00Z") == BASE
    assert parser.parse_dt("2026-11-16T06:30:00+01:00") == BASE
    assert parser.parse_dt("garbage") is None
    assert parser.parse_duration("PT1H5M") == timedelta(hours=1, minutes=5)
    assert parser.parse_duration("PT30S") == timedelta(seconds=30)
    assert parser.parse_duration(None) is None


def test_location_ref_xml() -> None:
    """Reference prefixes."""
    assert "<StopPointRef>at:1</StopPointRef>" in parser.location_ref_xml("at:1")
    assert "<StopPlaceRef>at:1</StopPlaceRef>" in parser.location_ref_xml("place:at:1")
    geo = parser.location_ref_xml("geo:47.07,15.45")
    assert "<Latitude>47.07</Latitude>" in geo and "<Longitude>15.45</Longitude>" in geo


def test_requests_are_well_formed() -> None:
    """Built requests are valid XML and escape input."""
    from xml.etree import ElementTree as ET

    for payload in (
        parser.build_location_request("A & B <C>"),
        parser.build_stop_event_request(
            "at:1", BASE, 10, onward=True, line_filter=[("L", "outward")]
        ),
        parser.build_trip_request("at:1", "geo:47,15", BASE, 5),
    ):
        ET.fromstring(parser.build_envelope(payload, "key&1", BASE))


def test_parse_locations() -> None:
    """Location search results."""
    stops = parser.parse_locations(_root("location.xml", "LocationResult"))
    assert [s.ref for s in stops] == ["at:46:4131", "at:46:4132"]
    assert stops[0].name == "Uni/Mozartgasse"
    assert stops[0].locality == "Graz"
    assert stops[0].label == "Uni/Mozartgasse (Graz)"
    assert stops[0].latitude == pytest.approx(47.07)


def test_parse_stop_events() -> None:
    """Departures, sorted by expected time, with real-time and cancellation."""
    deps = parser.parse_stop_events(_root("stop_events.xml", "StopEventResult"))
    assert len(deps) == 6
    first = deps[0]
    assert first.service.line == "58"
    assert first.service.destination == "Hauptbahnhof"
    assert first.service.direction_ref == "inward"
    assert first.service.mode == "bus"
    assert first.call.delay_min == 1
    assert first.call.bay == "1"
    assert [d.call.time for d in deps] == sorted(d.call.time for d in deps)
    assert any(d.service.cancelled for d in deps)
    assert deps[2].call.estimated is None  # line 63 without real-time


def test_filter_towards_lines_direction() -> None:
    """Client-side filters."""
    deps = parser.parse_stop_events(_root("stop_events.xml", "StopEventResult"))
    towards = filter_departures(deps, towards_ref="at:46:2000")
    assert {d.service.destination for d in towards} == {"Hauptbahnhof"}
    assert len(towards) == 3
    by_name = filter_departures(deps, towards_name="hauptbahn")
    assert by_name == towards
    assert {d.service.line for d in filter_departures(deps, lines=["63"])} == {"63"}
    inward = filter_departures(deps, direction_ref="INWARD")
    assert all(d.service.direction_ref == "inward" for d in inward) and len(inward) == 3


def test_error_without_results_raises() -> None:
    """ErrorMessage without results -> TriasApiError."""
    with pytest.raises(TriasApiError) as err:
        parser.parse_root(fixture("error_no_results.xml").encode(), "StopEventResult")
    assert "STOPEVENT_NOEVENTFOUND" in str(err.value)


def test_invalid_xml() -> None:
    """Non-TRIAS responses."""
    with pytest.raises(TriasParseError):
        parser.parse_root(b"<html>nope", "TripResult")
    with pytest.raises(TriasParseError):
        parser.parse_root(b"<html></html>", "TripResult")


def test_parse_trips_and_commute_logic() -> None:
    """Trips are attributed to the boarded station, dominated ones removed."""
    stations = [
        Place("at:46:1001", "Station A", 4),
        Place("at:46:1002", "Station B", 7),
        Place("at:46:1003", "Station C", 11),
    ]
    work = Place("at:46:2000", "Work", 3)
    options = []
    for station in stations:
        trips = parser.parse_trips(
            _root(f"trips_{station.ref.replace(':', '_')}.xml", "TripResult")
        )
        options += [o for t in trips if (o := make_option(t, station, work, stations))]

    # B's second trip walks to A and boards there -> attributed to A
    walked = [o for o in options if o.trip.legs[0].kind == "walk"]
    assert walked[0].station == "Station A"
    assert walked[0].leave == BASE + timedelta(minutes=11 - 4)

    result = sorted(pareto(dedupe(options)), key=lambda o: o.leave)
    assert [(o.station, o.leave - BASE) for o in result] == [
        ("Station B", timedelta(minutes=5)),  # 06:42 - 7
        ("Station A", timedelta(minutes=7)),  # 06:41 (delayed) - 4
        ("Station A", timedelta(minutes=21)),  # 06:55 - 4
    ]
    first = result[0]
    assert first.arrive == BASE + timedelta(minutes=35 + 3)
    assert first.trip.interchanges == 1
    assert first.as_dict()["lines"] == ["1", "7"]
    assert result[1].delay_min == 1
