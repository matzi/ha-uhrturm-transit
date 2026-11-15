#!/usr/bin/env python3
"""
Next connections home -> work when several home stations are possible.

How it works:
  1. One TripRequest per (home station, work station), starting at
     now + walking time to that station.
  2. Optionally one extra TripRequest from your home coordinates, letting the
     journey planner choose the station itself (cross-check).
  3. Every result gets a "leave home at" time = departure - your walk time
     to the station where the first vehicle is actually boarded.
  4. Duplicates and dominated options (another option leaves later and
     arrives no later) are removed; the rest is sorted by leave time.

Config: see commute.example.json

Examples:
  python commute.py -c commute.json
  python commute.py -c commute.json -n 8 --verbose
  python commute.py -c commute.json --all      # keep dominated options too
  python commute.py -c commute.json --json     # for Home Assistant
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import trias_client as tc


@dataclass
class Place:
    name: str
    ref: str
    walk_min: int


@dataclass
class Option:
    leave: datetime
    station: str          # where to board the first vehicle
    trip: tc.Trip
    departure: datetime
    arrive: datetime      # at work, incl. walk from the work station
    source: str           # "station" or "planner"


def _places(raw) -> list[Place]:
    if isinstance(raw, dict):
        raw = [raw]
    return [Place(r.get("name", r["ref"]), r["ref"], int(r.get("walk_min", 0))) for r in raw]


def load_config(path: str):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    coords = cfg.get("home_coords")          # [lat, lon] or null
    return _places(cfg["home"]), _places(cfg["work"]), coords


def make_option(t: tc.Trip, origin: Place | None, work: Place,
                home: list[Place], source: str) -> Option | None:
    pt = t.pt_legs
    if not pt:
        return None                          # pure walking trip
    first, last = pt[0], pt[-1]
    dep = first.board.time
    arr = last.alight.time
    if dep is None or arr is None:
        return None

    # Which of my home stations is actually boarded? (the planner may walk
    # from the requested station to a neighbouring one)
    hs = next((s for s in home if tc.same_stop(first.board.stop_ref, s.ref)), None)
    if hs is not None:
        leave = dep - timedelta(minutes=hs.walk_min)
        station = hs.name
    elif origin is not None:
        leave = (t.start or dep) - timedelta(minutes=origin.walk_min)
        station = f"{origin.name} → walk to {first.board.stop_name}"
    else:                                    # from coordinates, planner's walk
        leave = t.start or dep
        station = first.board.stop_name or "?"

    idx = max(i for i, l in enumerate(t.legs) if l.kind == "pt")
    trailing = sum((l.duration or timedelta() for l in t.legs[idx + 1:]), timedelta())
    arrive = arr + trailing + timedelta(minutes=work.walk_min)
    return Option(leave, station, t, dep, arrive, source)


def collect(home, work, coords, n_per: int, now: datetime):
    opts: list[Option] = []
    horizons: list[datetime] = []   # last leave time each query still covers

    def run(origin_ref, origin_place, w, when, source):
        try:
            ts = tc.trips(origin_ref, w.ref, when=when, n=n_per)
        except tc.TriasError as e:
            print(f"warning: {origin_ref} -> {w.ref}: {e}", file=sys.stderr)
            return
        got = [o for t in ts if (o := make_option(t, origin_place, w, home, source))]
        # only a query that hit the result limit may have hidden later options
        if got and len(ts) >= n_per:
            horizons.append(max(o.leave for o in got))
        opts.extend(got)

    for h in home:
        for w in work:
            run(h.ref, h, w, now + timedelta(minutes=h.walk_min), "station")
    if coords:
        for w in work:
            run(f"geo:{coords[0]},{coords[1]}", None, w, now, "planner")

    horizon = min(horizons) if horizons else None
    return opts, horizon


def dedupe(opts: list[Option]) -> list[Option]:
    best: dict[tuple, Option] = {}
    for o in opts:
        f, l = o.trip.pt_legs[0], o.trip.pt_legs[-1]
        key = (f.service.line, f.board.timetabled, l.service.line, l.alight.timetabled)
        cur = best.get(key)
        # prefer later leave time; prefer own station config over planner
        if cur is None or (o.leave, o.source == "station") > (cur.leave, cur.source == "station"):
            best[key] = o
    return list(best.values())


def pareto(opts: list[Option]) -> list[Option]:
    """Drop options where another one leaves later (or same) and arrives earlier (or same)."""
    ordered = sorted(opts, key=lambda o: (-o.leave.timestamp(), o.arrive, o.trip.interchanges))
    out, best_arrive = [], None
    for o in ordered:
        if best_arrive is None or o.arrive < best_arrive:
            out.append(o)
            best_arrive = o.arrive
    return out


def leg_summary(o: Option) -> list[dict]:
    res = []
    for l in o.trip.legs:
        if l.kind == "pt":
            res.append({
                "type": "pt",
                "line": l.service.line,
                "destination": l.service.destination,
                "mode": l.service.mode,
                "from": l.board.stop_name,
                "platform": l.board.bay,
                "dep_planned": l.board.timetabled.isoformat() if l.board.timetabled else None,
                "dep_expected": l.board.estimated.isoformat() if l.board.estimated else None,
                "to": l.alight.stop_name,
                "arr_planned": l.alight.timetabled.isoformat() if l.alight.timetabled else None,
                "arr_expected": l.alight.estimated.isoformat() if l.alight.estimated else None,
                "cancelled": l.service.cancelled,
            })
        else:
            res.append({
                "type": l.kind,
                "from": l.start_name,
                "to": l.end_name,
                "minutes": round(l.duration.total_seconds() / 60) if l.duration else None,
            })
    return res


def main() -> None:
    p = argparse.ArgumentParser(description="Next connections home -> work")
    p.add_argument("-c", "--config", default="commute.json")
    p.add_argument("-n", type=int, default=5, help="connections to show")
    p.add_argument("--per-station", type=int, default=6, help="trips requested per station")
    p.add_argument("--all", action="store_true", help="keep dominated options")
    p.add_argument("--verbose", action="store_true", help="print all legs")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()

    home, work, coords = load_config(a.config)
    now = tc.now()

    opts, horizon = collect(home, work, coords, a.per_station, now)
    opts = [o for o in opts if o.leave >= now - timedelta(minutes=1)]
    if horizon:
        opts = [o for o in opts if o.leave <= horizon]
    opts = dedupe(opts)
    if not a.all:
        opts = pareto(opts)
    opts = sorted(opts, key=lambda o: (o.leave, o.arrive))[: a.n]

    if a.json:
        out = []
        for o in opts:
            first = o.trip.pt_legs[0]
            out.append({
                "leave": o.leave.isoformat(),
                "leave_in_min": max(0, int((o.leave - now).total_seconds() // 60)),
                "station": o.station,
                "departure": o.departure.isoformat(),
                "delay_min": first.board.delay_min,
                "lines": [l.service.line for l in o.trip.pt_legs],
                "arrive": o.arrive.isoformat(),
                "duration_min": round((o.arrive - o.leave).total_seconds() / 60),
                "changes": o.trip.interchanges,
                "cancelled": any(l.service.cancelled for l in o.trip.pt_legs),
                "source": o.source,
                "legs": leg_summary(o),
            })
        print(json.dumps({
            "generated": now.isoformat(),
            "next_station": out[0]["station"] if out else None,
            "next_leave_in_min": out[0]["leave_in_min"] if out else None,
            "connections": out,
        }, ensure_ascii=False, indent=2))
        return

    if not opts:
        print("No connections found.", file=sys.stderr)
        return

    print(f"Home → work  ({now:%H:%M})")
    print(f"{'Leave':<6} {'in':<5} {'Go to':<24} {'Dep':<11} {'Route':<20} {'Arr':<6} {'Dur':<5} Chg")
    for o in opts:
        first = o.trip.pt_legs[0]
        d = first.board.delay_min
        dep = f"{o.departure:%H:%M}" + (f" +{d}" if d and d > 0 else "")
        route = " → ".join(l.service.line or "?" for l in o.trip.pt_legs)
        dur = round((o.arrive - o.leave).total_seconds() / 60)
        mins = max(0, int((o.leave - now).total_seconds() // 60))
        flag = "  ⚠ cancelled" if any(l.service.cancelled for l in o.trip.pt_legs) else ""
        print(f"{o.leave:%H:%M}  {str(mins) + chr(39):<5} {o.station[:24]:<24} {dep:<11} "
              f"{route[:20]:<20} {o.arrive:%H:%M}  {str(dur) + chr(39):<5} {o.trip.interchanges}{flag}")
        if a.verbose:
            for l in o.trip.legs:
                if l.kind == "pt":
                    bt, at = l.board.time, l.alight.time
                    print(f"        {bt:%H:%M} {l.service.line:<5} {l.board.stop_name}"
                          f"{' (' + l.board.bay + ')' if l.board.bay else ''}"
                          f" → {at:%H:%M} {l.alight.stop_name}")
                else:
                    m = round(l.duration.total_seconds() / 60) if l.duration else "?"
                    print(f"              {l.kind} {m}' {l.start_name or ''} → {l.end_name or ''}")


if __name__ == "__main__":
    try:
        main()
    except tc.TriasError as e:
        sys.exit(f"Error: {e}")
