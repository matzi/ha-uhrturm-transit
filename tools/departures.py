#!/usr/bin/env python3
"""
Next departures at a stop, with direction filters.

TRIAS itself only offers a server-side LineFilter (LineRef + optional
DirectionRef). A "towards stop X" filter does not exist in the API, so this
script does it client-side using the onward calls of each departure.

Examples:
  # everything
  python departures.py at:42:1234

  # only vehicles that later stop at a given stop (ID or part of the name)
  python departures.py at:42:1234 --towards at:42:5678
  python departures.py at:42:1234 --towards "Heiligengeistplatz"

  # only some lines / destination text
  python departures.py at:42:1234 --line 5 --line S1
  python departures.py at:42:1234 --dest Hauptbahnhof

  # discover LineRef/DirectionRef, then filter server-side
  python departures.py at:42:1234 --show-refs
  python departures.py at:42:1234 --line-ref "kvg:05005: :H:j26" --direction-ref outward

  # JSON (for Home Assistant command_line sensor)
  python departures.py at:42:1234 --towards at:42:5678 --json
"""
from __future__ import annotations

import argparse
import json
import sys

import trias_client as tc


def main() -> None:
    p = argparse.ArgumentParser(description="Next departures at a TRIAS stop")
    p.add_argument("stop", help="stop ID (find it with: trias_client.py search NAME)")
    p.add_argument("-n", type=int, default=10, help="number of departures to show")
    p.add_argument("--towards", action="append", default=[],
                   help="keep departures that later stop here (stop ID or name part); repeatable (OR)")
    p.add_argument("--line", action="append", default=[],
                   help="keep only these line names, e.g. 5, S1; repeatable")
    p.add_argument("--dest", action="append", default=[],
                   help="keep if destination text contains this; repeatable (OR)")
    p.add_argument("--line-ref", help="server-side TRIAS LineFilter: LineRef")
    p.add_argument("--direction-ref",
                   help="keep only this DirectionRef, e.g. inward/outward (client-side; "
                        "also sent server-side together with --line-ref)")
    p.add_argument("--fetch", type=int,
                   help="departures to request from the server (default: n, or max(4n, 40) with client filters)")
    p.add_argument("--show-refs", action="store_true", help="show LineRef / DirectionRef columns")
    p.add_argument("--json", action="store_true", help="JSON output")
    a = p.parse_args()

    client_filter = bool(a.towards or a.line or a.dest or a.direction_ref)
    fetch = a.fetch or (max(a.n * 4, 40) if client_filter else a.n)
    lf = [(a.line_ref, a.direction_ref)] if a.line_ref else None

    deps = tc.stop_events(a.stop, n=fetch, onward=bool(a.towards), line_filter=lf)

    lines = {l.lower() for l in a.line}

    def keep(d: tc.Departure) -> bool:
        if lines and (d.service.line or "").lower() not in lines:
            return False
        if a.direction_ref and (d.service.direction_ref or "").lower() != a.direction_ref.lower():
            return False
        dest = (d.service.destination or "").lower()
        if a.dest and not any(x.lower() in dest for x in a.dest):
            return False
        if a.towards and not any(d.passes(t) for t in a.towards):
            return False
        return True

    shown = [d for d in deps if keep(d)][: a.n]
    now = tc.now()

    if a.json:
        out = []
        for d in shown:
            t = d.call.time
            out.append({
                "line": d.service.line,
                "destination": d.service.destination,
                "mode": d.service.mode,
                "planned": d.call.timetabled.isoformat() if d.call.timetabled else None,
                "expected": d.call.estimated.isoformat() if d.call.estimated else None,
                "delay_min": d.call.delay_min,
                "realtime": d.call.estimated is not None,
                "minutes": max(0, int((t - now).total_seconds() // 60)) if t else None,
                "platform": d.call.bay,
                "cancelled": d.service.cancelled,
                "line_ref": d.service.line_ref,
                "direction_ref": d.service.direction_ref,
            })
        print(json.dumps({
            "stop": a.stop,
            "stop_name": shown[0].call.stop_name if shown else None,
            "next_in_min": out[0]["minutes"] if out else None,
            "departures": out,
        }, ensure_ascii=False, indent=2))
        return

    if not shown:
        print(f"No matching departures in the next {len(deps)} fetched "
              f"(try a larger --fetch).", file=sys.stderr)
        return

    print(f"Departures from {shown[0].call.stop_name or a.stop}  ({now:%H:%M})")
    for d in shown:
        t = d.call.time
        mins = max(0, int((t - now).total_seconds() // 60)) if t else None
        planned = f"{d.call.timetabled:%H:%M}" if d.call.timetabled else "--:--"
        if d.service.cancelled:
            rt = "CANCELLED"
        elif d.call.delay_min is None:
            rt = ""                                  # no real-time data
        else:
            rt = f"+{d.call.delay_min}" if d.call.delay_min > 0 else "on time"
        row = (f"{planned}  {rt:<9} {('in ' + str(mins) + "'") if mins is not None else '':<8}"
               f"{d.service.line or '?':<6} {d.service.destination or '':<30} {d.call.bay or ''}")
        if a.show_refs:
            row += f"   [{d.service.line_ref} | {d.service.direction_ref}]"
        print(row)


if __name__ == "__main__":
    try:
        main()
    except tc.TriasError as e:
        sys.exit(f"Error: {e}")
