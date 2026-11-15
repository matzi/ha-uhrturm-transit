"""Build TRIAS 1.2 requests and parse responses (pure functions, no I/O)."""

from __future__ import annotations

from datetime import datetime, timedelta, UTC
import re
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from .exceptions import TriasApiError, TriasParseError
from .models import Call, Departure, Leg, ServiceInfo, StopInfo, Trip

NS = {"t": "http://www.vdv.de/trias", "siri": "http://www.siri.org.uk/siri"}
_T = "{" + NS["t"] + "}"
_FAR_FUTURE = datetime.max.replace(tzinfo=UTC)
_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


# --------------------------------------------------------------------------- helpers


def fmt_dt(dt: datetime) -> str:
    """Format an aware datetime as xs:dateTime with offset."""
    return dt.isoformat(timespec="seconds")


def parse_dt(value: str | None) -> datetime | None:
    """Parse xs:dateTime. Naive values are treated as UTC."""
    if not value:
        return None
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def parse_duration(value: str | None) -> timedelta | None:
    """Parse an ISO 8601 duration like PT1H5M."""
    if not value:
        return None
    match = _DUR.match(value.strip())
    if not match:
        return None
    days, hours, minutes, seconds = match.groups()
    return timedelta(
        days=int(days or 0),
        hours=int(hours or 0),
        minutes=int(minutes or 0),
        seconds=float(seconds or 0),
    )


def _text(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.find(path, NS)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def location_ref_xml(ref: str) -> str:
    """Build a LocationRef.

    ``at:42:1234`` -> StopPointRef, ``place:at:42:1234`` -> StopPlaceRef,
    ``geo:46.62,14.30`` (lat,lon) -> GeoPosition.
    """
    if ref.startswith("geo:"):
        lat, lon = (float(x) for x in ref[4:].split(","))
        return (
            f"<LocationRef><GeoPosition><Longitude>{lon}</Longitude>"
            f"<Latitude>{lat}</Latitude></GeoPosition></LocationRef>"
        )
    if ref.startswith("place:"):
        return f"<LocationRef><StopPlaceRef>{escape(ref[6:])}</StopPlaceRef></LocationRef>"
    return f"<LocationRef><StopPointRef>{escape(ref)}</StopPointRef></LocationRef>"


# --------------------------------------------------------------------------- requests


def build_envelope(payload: str, requestor_ref: str, now: datetime) -> str:
    """Wrap a request payload in a TRIAS ServiceRequest."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Trias version="1.2" xmlns="http://www.vdv.de/trias" '
        'xmlns:siri="http://www.siri.org.uk/siri">\n'
        "  <ServiceRequest>\n"
        f"    <siri:RequestTimestamp>{fmt_dt(now)}</siri:RequestTimestamp>\n"
        f"    <siri:RequestorRef>{escape(requestor_ref)}</siri:RequestorRef>\n"
        "    <RequestPayload>\n"
        f"{payload}\n"
        "    </RequestPayload>\n"
        "  </ServiceRequest>\n"
        "</Trias>\n"
    )


def build_location_request(name: str, count: int = 10) -> str:
    """LocationInformationRequest for stops by name."""
    return (
        "      <LocationInformationRequest>\n"
        f"        <InitialInput><LocationName>{escape(name)}</LocationName></InitialInput>\n"
        f"        <Restrictions><Type>stop</Type><NumberOfResults>{count}</NumberOfResults></Restrictions>\n"
        "      </LocationInformationRequest>"
    )


def build_stop_event_request(
    stop_ref: str,
    when: datetime,
    count: int,
    *,
    onward: bool = False,
    line_filter: list[tuple[str, str | None]] | None = None,
) -> str:
    """StopEventRequest (departures)."""
    lf = ""
    if line_filter:
        lines = "".join(
            f"<Line><LineRef>{escape(line)}</LineRef>"
            + (f"<DirectionRef>{escape(direction)}</DirectionRef>" if direction else "")
            + "</Line>"
            for line, direction in line_filter
        )
        lf = f"<LineFilter>{lines}<Exclude>false</Exclude></LineFilter>"
    return (
        "      <StopEventRequest>\n"
        "        <Location>\n"
        f"          {location_ref_xml(stop_ref)}\n"
        f"          <DepArrTime>{fmt_dt(when)}</DepArrTime>\n"
        "        </Location>\n"
        "        <Params>\n"
        f"          {lf}\n"
        f"          <NumberOfResults>{count}</NumberOfResults>\n"
        "          <StopEventType>departure</StopEventType>\n"
        "          <IncludePreviousCalls>false</IncludePreviousCalls>\n"
        f"          <IncludeOnwardCalls>{'true' if onward else 'false'}</IncludeOnwardCalls>\n"
        "          <IncludeRealtimeData>true</IncludeRealtimeData>\n"
        "        </Params>\n"
        "      </StopEventRequest>"
    )


def build_trip_request(origin: str, destination: str, when: datetime, count: int) -> str:
    """TripRequest (journey planner)."""
    return (
        "      <TripRequest>\n"
        "        <Origin>\n"
        f"          {location_ref_xml(origin)}\n"
        f"          <DepArrTime>{fmt_dt(when)}</DepArrTime>\n"
        "        </Origin>\n"
        "        <Destination>\n"
        f"          {location_ref_xml(destination)}\n"
        "        </Destination>\n"
        "        <Params>\n"
        f"          <NumberOfResults>{count}</NumberOfResults>\n"
        "          <IncludeTrackSections>false</IncludeTrackSections>\n"
        "          <IncludeLegProjection>false</IncludeLegProjection>\n"
        "          <IncludeIntermediateStops>false</IncludeIntermediateStops>\n"
        "        </Params>\n"
        "      </TripRequest>"
    )


# --------------------------------------------------------------------------- responses


def parse_root(content: bytes, result_tag: str) -> tuple[ET.Element, list[str]]:
    """Parse response XML, raise on TRIAS errors without results.

    Returns the root and non-fatal warnings.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError as err:
        raise TriasParseError(f"invalid XML: {err}") from err
    if root.tag != f"{_T}Trias":
        raise TriasParseError(f"unexpected root element {root.tag}")

    messages = []
    for em in root.iter(f"{_T}ErrorMessage"):
        code = _text(em, "t:Code") or ""
        text = _text(em, "t:Text/t:Text") or ""
        messages.append(f"{code} {text}".strip())
    has_results = next(root.iter(f"{_T}{result_tag}"), None) is not None
    if messages and not has_results:
        raise TriasApiError(messages)
    return root, messages


def _parse_call(el: ET.Element | None, kind: str) -> Call:
    other = "Arrival" if kind == "Departure" else "Departure"
    svc = None
    if el is not None:
        svc = el.find(f"t:Service{kind}", NS)
        if svc is None:
            svc = el.find(f"t:Service{other}", NS)
    return Call(
        stop_ref=_text(el, "t:StopPointRef"),
        stop_name=_text(el, "t:StopPointName/t:Text"),
        bay=_text(el, "t:EstimatedBay/t:Text") or _text(el, "t:PlannedBay/t:Text"),
        timetabled=parse_dt(_text(svc, "t:TimetabledTime")),
        estimated=parse_dt(_text(svc, "t:EstimatedTime")),
    )


def _parse_service(svc: ET.Element | None) -> ServiceInfo:
    # ".//" works for TRIAS 1.1 (fields directly in Service) and
    # 1.2 (fields inside Service/ServiceSection)
    return ServiceInfo(
        line=_text(svc, ".//t:PublishedLineName/t:Text") or _text(svc, ".//t:LineRef"),
        destination=_text(svc, ".//t:DestinationText/t:Text"),
        mode=_text(svc, ".//t:Mode/t:PtMode"),
        line_ref=_text(svc, ".//t:LineRef"),
        direction_ref=_text(svc, ".//t:DirectionRef"),
        journey_ref=_text(svc, "t:JourneyRef"),
        cancelled=(_text(svc, "t:Cancelled") or "").lower() == "true",
    )


def _loc_name(el: ET.Element | None) -> str | None:
    return (
        _text(el, "t:StopPointName/t:Text")
        or _text(el, "t:StopPlaceName/t:Text")
        or _text(el, "t:LocationName/t:Text")
    )


def parse_locations(root: ET.Element) -> list[StopInfo]:
    """Parse a LocationInformationResponse."""
    result = []
    seen = set()
    for loc in root.iterfind(".//t:LocationResult/t:Location", NS):
        sp, spl = loc.find("t:StopPoint", NS), loc.find("t:StopPlace", NS)
        if sp is not None:
            ref = _text(sp, "t:StopPointRef")
            name = _text(sp, "t:StopPointName/t:Text")
            kind = "StopPoint"
        elif spl is not None:
            ref = _text(spl, "t:StopPlaceRef")
            name = _text(spl, "t:StopPlaceName/t:Text")
            kind = "StopPlace"
        else:
            continue
        if not ref or ref in seen:
            continue
        seen.add(ref)
        lat = _text(loc, "t:GeoPosition/t:Latitude")
        lon = _text(loc, "t:GeoPosition/t:Longitude")
        result.append(
            StopInfo(
                ref=ref,
                name=name,
                locality=_text(loc, "t:LocationName/t:Text"),
                kind=kind,
                latitude=float(lat) if lat else None,
                longitude=float(lon) if lon else None,
            )
        )
    return result


def parse_stop_events(root: ET.Element) -> list[Departure]:
    """Parse a StopEventResponse, sorted by best known time."""
    deps = []
    for se in root.iterfind(".//t:StopEventResult/t:StopEvent", NS):
        this = se.find("t:ThisCall/t:CallAtStop", NS)
        svc = se.find("t:Service", NS)
        if this is None or svc is None:
            continue
        deps.append(
            Departure(
                service=_parse_service(svc),
                call=_parse_call(this, "Departure"),
                onward=[
                    _parse_call(c, "Arrival") for c in se.iterfind("t:OnwardCall/t:CallAtStop", NS)
                ],
            )
        )
    deps.sort(key=lambda d: d.call.time or _FAR_FUTURE)
    return deps


def parse_trips(root: ET.Element) -> list[Trip]:
    """Parse a TripResponse."""
    result = []
    for tr in root.iterfind(".//t:TripResult/t:Trip", NS):
        legs: list[Leg] = []
        for leg in tr.iterfind("t:TripLeg", NS):
            tl = leg.find("t:TimedLeg", NS)
            if tl is not None:
                legs.append(
                    Leg(
                        kind="pt",
                        service=_parse_service(tl.find("t:Service", NS)),
                        board=_parse_call(tl.find("t:LegBoard", NS), "Departure"),
                        alight=_parse_call(tl.find("t:LegAlight", NS), "Arrival"),
                    )
                )
                continue
            for tag, kind in (("t:ContinuousLeg", "walk"), ("t:InterchangeLeg", "interchange")):
                cl = leg.find(tag, NS)
                if cl is not None:
                    legs.append(
                        Leg(
                            kind=kind,
                            start_name=_loc_name(cl.find("t:LegStart", NS)),
                            end_name=_loc_name(cl.find("t:LegEnd", NS)),
                            start_time=parse_dt(_text(cl, "t:TimeWindowStart")),
                            end_time=parse_dt(_text(cl, "t:TimeWindowEnd")),
                            duration=parse_duration(_text(cl, "t:Duration")),
                        )
                    )
                    break
        n_pt = sum(1 for leg in legs if leg.kind == "pt")
        interchanges = _text(tr, "t:Interchanges")
        result.append(
            Trip(
                start=parse_dt(_text(tr, "t:StartTime")),
                end=parse_dt(_text(tr, "t:EndTime")),
                duration=parse_duration(_text(tr, "t:Duration")),
                interchanges=int(interchanges) if interchanges else max(0, n_pt - 1),
                legs=legs,
            )
        )
    return result
