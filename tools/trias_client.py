#!/usr/bin/env python3
"""
Minimal TRIAS 1.2 (VDV 431-2) client: stop search, departures, trips.

Configuration via environment variables:
  TRIAS_URL            endpoint URL
  TRIAS_REQUESTOR_REF  your API key / requestor ref (sent as siri:RequestorRef)
  TRIAS_DUMP=1         write last request/response to ./trias_last_request.xml
                       and ./trias_last_response.xml (handy for debugging)

As a script, find stop IDs:
  python trias_client.py search "Klagenfurt Hbf"

Stop references accepted everywhere:
  at:42:1234          -> <StopPointRef>
  place:at:42:1234    -> <StopPlaceRef>
  geo:46.6247,14.3053 -> <GeoPosition> (lat,lon)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import requests

NS = {"t": "http://www.vdv.de/trias", "siri": "http://www.siri.org.uk/siri"}
TIMEOUT = 20
_FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)


class TriasError(RuntimeError):
    pass


# --------------------------------------------------------------------------- helpers

def now() -> datetime:
    return datetime.now().astimezone()


def fmt_dt(dt: datetime) -> str:
    return dt.astimezone().isoformat(timespec="seconds")


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:          # no offset given -> assume local time
        dt = dt.astimezone()
    return dt.astimezone()


_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


def parse_duration(s: str | None) -> timedelta | None:
    if not s:
        return None
    m = _DUR.match(s.strip())
    if not m:
        return None
    d, h, mi, sec = m.groups()
    return timedelta(days=int(d or 0), hours=int(h or 0),
                     minutes=int(mi or 0), seconds=float(sec or 0))


def _text(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    f = el.find(path, NS)
    if f is None or f.text is None:
        return None
    return f.text.strip() or None


def same_stop(ref: str | None, target: str | None) -> bool:
    """True if both refs denote the same stop, also across hierarchy levels
    (platform at:42:1234:0:2 matches station at:42:1234 and vice versa)."""
    if not ref or not target:
        return False
    return ref == target or ref.startswith(target + ":") or target.startswith(ref + ":")


def location_ref_xml(ref: str) -> str:
    if ref.startswith("geo:"):
        lat, lon = (float(x) for x in ref[4:].split(","))
        return (f"<LocationRef><GeoPosition><Longitude>{lon}</Longitude>"
                f"<Latitude>{lat}</Latitude></GeoPosition></LocationRef>")
    if ref.startswith("place:"):
        return f"<LocationRef><StopPlaceRef>{escape(ref[6:])}</StopPlaceRef></LocationRef>"
    return f"<LocationRef><StopPointRef>{escape(ref)}</StopPointRef></LocationRef>"


# --------------------------------------------------------------------------- transport

def post(payload: str) -> ET.Element:
    url = os.environ.get("TRIAS_URL")
    if not url:
        raise TriasError("TRIAS_URL is not set")
    requestor = os.environ.get("TRIAS_REQUESTOR_REF", "")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Trias version="1.2" xmlns="http://www.vdv.de/trias" xmlns:siri="http://www.siri.org.uk/siri">
  <ServiceRequest>
    <siri:RequestTimestamp>{fmt_dt(now())}</siri:RequestTimestamp>
    <siri:RequestorRef>{escape(requestor)}</siri:RequestorRef>
    <RequestPayload>
{payload}
    </RequestPayload>
  </ServiceRequest>
</Trias>
"""
    dump = bool(os.environ.get("TRIAS_DUMP"))
    if dump:
        Path("trias_last_request.xml").write_text(xml, encoding="utf-8")

    r = requests.post(url, data=xml.encode("utf-8"),
                      headers={"Content-Type": "text/xml; charset=utf-8"},
                      timeout=TIMEOUT)
    if dump:
        Path("trias_last_response.xml").write_bytes(r.content)
    if r.status_code != 200:
        raise TriasError(f"HTTP {r.status_code}: {r.text[:500]}")

    root = ET.fromstring(r.content)

    errors = []
    for em in root.iter(f"{{{NS['t']}}}ErrorMessage"):
        code = _text(em, "t:Code") or ""
        txt = _text(em, "t:Text/t:Text") or ""
        errors.append(f"{code} {txt}".strip())
    has_results = any(root.iter(f"{{{NS['t']}}}{tag}")
                      for tag in ("StopEventResult", "TripResult", "LocationResult"))
    if errors and not has_results:
        raise TriasError("; ".join(errors))
    for e in errors:
        print(f"TRIAS warning: {e}", file=sys.stderr)
    return root


# --------------------------------------------------------------------------- data classes

@dataclass
class Call:
    stop_ref: str | None
    stop_name: str | None
    bay: str | None
    timetabled: datetime | None
    estimated: datetime | None

    @property
    def time(self) -> datetime | None:
        return self.estimated or self.timetabled

    @property
    def delay_min(self) -> int | None:
        if self.estimated and self.timetabled:
            return round((self.estimated - self.timetabled).total_seconds() / 60)
        return None


@dataclass
class ServiceInfo:
    line: str | None
    destination: str | None
    mode: str | None
    line_ref: str | None
    direction_ref: str | None
    journey_ref: str | None
    cancelled: bool


@dataclass
class Departure:
    service: ServiceInfo
    call: Call
    onward: list[Call] = field(default_factory=list)

    def passes(self, target: str) -> bool:
        """Does this vehicle stop at `target` later? target = stop ID or part of a name."""
        if ":" in target:
            return any(same_stop(c.stop_ref, target) for c in self.onward)
        t = target.lower()
        return any(c.stop_name and t in c.stop_name.lower() for c in self.onward)


@dataclass
class Leg:
    kind: str                         # "pt" | "walk" | "interchange"
    service: ServiceInfo | None = None
    board: Call | None = None
    alight: Call | None = None
    start_name: str | None = None
    end_name: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration: timedelta | None = None


@dataclass
class Trip:
    start: datetime | None
    end: datetime | None
    duration: timedelta | None
    interchanges: int
    legs: list[Leg]

    @property
    def pt_legs(self) -> list[Leg]:
        return [l for l in self.legs if l.kind == "pt"]


@dataclass
class StopInfo:
    ref: str
    name: str | None
    locality: str | None
    kind: str
    lat: float | None
    lon: float | None


# --------------------------------------------------------------------------- parsing

def _parse_call(el: ET.Element | None, kind: str) -> Call:
    """kind = 'Departure' or 'Arrival' (falls back to the other one)."""
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
    # ".//" so it works for TRIAS 1.1 (fields directly in Service) and
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
    return (_text(el, "t:StopPointName/t:Text")
            or _text(el, "t:StopPlaceName/t:Text")
            or _text(el, "t:LocationName/t:Text"))


# --------------------------------------------------------------------------- requests

def search_stops(name: str, n: int = 10) -> list[StopInfo]:
    payload = f"""      <LocationInformationRequest>
        <InitialInput><LocationName>{escape(name)}</LocationName></InitialInput>
        <Restrictions><Type>stop</Type><NumberOfResults>{n}</NumberOfResults></Restrictions>
      </LocationInformationRequest>"""
    root = post(payload)
    out = []
    for loc in root.iterfind(".//t:LocationResult/t:Location", NS):
        sp, spl = loc.find("t:StopPoint", NS), loc.find("t:StopPlace", NS)
        if sp is not None:
            ref, nm, kind = _text(sp, "t:StopPointRef"), _text(sp, "t:StopPointName/t:Text"), "StopPoint"
        elif spl is not None:
            ref, nm, kind = _text(spl, "t:StopPlaceRef"), _text(spl, "t:StopPlaceName/t:Text"), "StopPlace"
        else:
            continue
        lat, lon = _text(loc, "t:GeoPosition/t:Latitude"), _text(loc, "t:GeoPosition/t:Longitude")
        out.append(StopInfo(ref=ref or "?", name=nm, locality=_text(loc, "t:LocationName/t:Text"),
                            kind=kind, lat=float(lat) if lat else None,
                            lon=float(lon) if lon else None))
    return out


def stop_events(stop_ref: str, when: datetime | None = None, n: int = 20,
                onward: bool = False,
                line_filter: list[tuple[str, str | None]] | None = None) -> list[Departure]:
    """Next departures at a stop.

    line_filter: server-side TRIAS LineFilter, list of (LineRef, DirectionRef or None).
    onward:      include the onward calls (needed for 'towards' filtering).
    """
    lf = ""
    if line_filter:
        lines = "".join(
            f"<Line><LineRef>{escape(l)}</LineRef>"
            + (f"<DirectionRef>{escape(d)}</DirectionRef>" if d else "")
            + "</Line>"
            for l, d in line_filter)
        lf = f"<LineFilter>{lines}<Exclude>false</Exclude></LineFilter>"
    payload = f"""      <StopEventRequest>
        <Location>
          {location_ref_xml(stop_ref)}
          <DepArrTime>{fmt_dt(when or now())}</DepArrTime>
        </Location>
        <Params>
          {lf}
          <NumberOfResults>{n}</NumberOfResults>
          <StopEventType>departure</StopEventType>
          <IncludePreviousCalls>false</IncludePreviousCalls>
          <IncludeOnwardCalls>{'true' if onward else 'false'}</IncludeOnwardCalls>
          <IncludeRealtimeData>true</IncludeRealtimeData>
        </Params>
      </StopEventRequest>"""
    root = post(payload)
    deps = []
    for se in root.iterfind(".//t:StopEventResult/t:StopEvent", NS):
        this = se.find("t:ThisCall/t:CallAtStop", NS)
        svc = se.find("t:Service", NS)
        if this is None or svc is None:
            continue
        deps.append(Departure(
            service=_parse_service(svc),
            call=_parse_call(this, "Departure"),
            onward=[_parse_call(c, "Arrival")
                    for c in se.iterfind("t:OnwardCall/t:CallAtStop", NS)],
        ))
    deps.sort(key=lambda d: d.call.time or _FAR_FUTURE)
    return deps


def trips(origin: str, destination: str, when: datetime | None = None,
          n: int = 5) -> list[Trip]:
    payload = f"""      <TripRequest>
        <Origin>
          {location_ref_xml(origin)}
          <DepArrTime>{fmt_dt(when or now())}</DepArrTime>
        </Origin>
        <Destination>
          {location_ref_xml(destination)}
        </Destination>
        <Params>
          <NumberOfResults>{n}</NumberOfResults>
          <IncludeTrackSections>false</IncludeTrackSections>
          <IncludeLegProjection>false</IncludeLegProjection>
          <IncludeIntermediateStops>false</IncludeIntermediateStops>
        </Params>
      </TripRequest>"""
    root = post(payload)
    result = []
    for tr in root.iterfind(".//t:TripResult/t:Trip", NS):
        legs: list[Leg] = []
        for leg in tr.iterfind("t:TripLeg", NS):
            tl = leg.find("t:TimedLeg", NS)
            if tl is not None:
                legs.append(Leg(
                    kind="pt",
                    service=_parse_service(tl.find("t:Service", NS)),
                    board=_parse_call(tl.find("t:LegBoard", NS), "Departure"),
                    alight=_parse_call(tl.find("t:LegAlight", NS), "Arrival"),
                ))
                continue
            for tag, kind in (("t:ContinuousLeg", "walk"), ("t:InterchangeLeg", "interchange")):
                cl = leg.find(tag, NS)
                if cl is not None:
                    legs.append(Leg(
                        kind=kind,
                        start_name=_loc_name(cl.find("t:LegStart", NS)),
                        end_name=_loc_name(cl.find("t:LegEnd", NS)),
                        start_time=parse_dt(_text(cl, "t:TimeWindowStart")),
                        end_time=parse_dt(_text(cl, "t:TimeWindowEnd")),
                        duration=parse_duration(_text(cl, "t:Duration")),
                    ))
                    break
        n_pt = sum(1 for l in legs if l.kind == "pt")
        ic = _text(tr, "t:Interchanges")
        result.append(Trip(
            start=parse_dt(_text(tr, "t:StartTime")),
            end=parse_dt(_text(tr, "t:EndTime")),
            duration=parse_duration(_text(tr, "t:Duration")),
            interchanges=int(ic) if ic else max(0, n_pt - 1),
            legs=legs,
        ))
    return result


# --------------------------------------------------------------------------- CLI

def _main() -> None:
    p = argparse.ArgumentParser(description="TRIAS helper")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="find stop IDs by name")
    s.add_argument("name")
    s.add_argument("-n", type=int, default=10)
    a = p.parse_args()

    if a.cmd == "search":
        for st in search_stops(a.name, a.n):
            loc = f" ({st.locality})" if st.locality and st.locality != st.name else ""
            print(f"{st.ref:<28} {st.kind:<9} {st.name}{loc}")


if __name__ == "__main__":
    try:
        _main()
    except TriasError as e:
        sys.exit(f"Error: {e}")
