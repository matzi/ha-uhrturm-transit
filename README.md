# Uhrturm Transit (TRIAS) for Home Assistant

Departure boards and "when do I have to leave?" for any public transport provider
that offers a **TRIAS** (VDV 431-2) interface, e.g. Austrian and German transport
associations using EFA.

Named after Graz's iconic clock tower, this integration was built and tested for
**Graz and Steiermark (Verkehrsverbund Steiermark / Verbundlinie)**, but works
out of the box with **any TRIAS-compatible API endpoint** across Austria, Germany,
and Switzerland.

- **Departure boards** with a real *direction* filter: only vehicles that later stop at
  a chosen stop ("towards Hauptbahnhof"), optionally limited to lines.
- **Commutes** with several possible home stations: tells you *when to leave* and
  *which station to walk to*, taking your walking times into account.
- Real-time delays, cancellations, a disruption binary sensor per board/commute.
- Polls only when useful: commutes only inside a time window, boards slow down when
  nothing leaves soon.
- Actions for ad-hoc queries (`uhrturm_oepnv.search_stops`, `uhrturm_oepnv.get_departures`, `uhrturm_oepnv.plan_trip`).
- UI configuration, English and German.

## Installation

### HACS (custom repository)

1. HACS → ⋮ → *Custom repositories* → add `https://github.com/matzi/ha-uhrturm-transit`,
   category *Integration*.
2. Install *Uhrturm Transit (TRIAS)*, restart Home Assistant.

### Manual

Copy `custom_components/uhrturm_oepnv` to `<config>/custom_components/uhrturm_oepnv` and restart.

Requires Home Assistant 2026.1 or newer.

## Setup

1. *Settings → Devices & services → Add integration → Uhrturm Transit (TRIAS).*
   Enter the endpoint URL and your requestor ref / API key from your provider.
2. On the integration page add as many **departure boards** and **commutes** as you
   like (buttons *Add departure board* / *Add commute*). Every one becomes a device.

### Departure board

Search the stop, pick it, then set:

| Option | Meaning |
|---|---|
| Towards (stop) | Only vehicles that stop there later on. You search by name and pick a stop – or choose the `(*)` entry to match every stop whose name contains the text. |
| Lines | Comma-separated line names, empty = all |
| Direction ref | Provider's DirectionRef (`inward`/`outward`, `H`/`R`). Defined per line – combine with *Lines*. Usually *Towards* is the better choice. |
| Walking time to the stop | Departures you cannot reach anymore are hidden |
| Disruption threshold | Delay (min) that turns the disruption sensor on |
| Update interval | Seconds; automatically 5 min while nothing leaves within 30 min |

### Commute

Add one or more home stations with the walking time from home, then the destination
stop with the walking time from it, then weekdays and a time window. Connections are
only requested inside the window.

For every station a trip request is made; each result gets a *leave home at* time
(departure − walking time to the station where you actually board). Identical and
dominated connections (another one leaves later and arrives no later) are dropped.

## Entities

**Departure board**

| Entity | State | Attributes |
|---|---|---|
| `sensor.<board>_departure_1` | timestamp (real-time) of the next departure | `index`, `line`, `destination`, `delay`, `platform`, `planned`, `realtime`, `departures` (list) |
| `sensor.<board>_departure_2` … `_departure_<n>` | timestamp of the 2nd … n-th departure | same, without list |
| `binary_sensor.<board>_disruption` | on if a shown departure is cancelled or late | `affected` |

*Number of departures* in the board settings sets n (1–20, default 5). Cancelled
vehicles are skipped by the numbered sensors (they still appear in `departures` and
trigger the disruption sensor). Lowering n removes the surplus sensors. Boards created
before this version keep their `…_next_departure` / `…_following_departure` entity IDs
for departure 1 and 2.

**Commute**

| Entity | State | Attributes |
|---|---|---|
| `sensor.<commute>_leave_at` | timestamp: leave home | `station`, `board_stop`, `lines`, `departure`, `arrival`, `changes`, `delay`, `active`, `connections` (list) |
| `sensor.<commute>_station` | enum: station to walk to | |
| `sensor.<commute>_departure` | timestamp of the first vehicle | |
| `sensor.<commute>_arrival` | timestamp at work (incl. walk) | |
| `sensor.<commute>_delay` | minutes | |
| `binary_sensor.<commute>_disruption` | on if the next connection is cancelled/late | |

Outside the time window the commute sensors are `unknown`. The large list attributes
(`departures`, `connections`) are excluded from the recorder.

## Dashboard card

The integration ships a departure card; it is loaded automatically (no resource to
add). *Edit dashboard → Add card → Uhrturm departures*, or in YAML:

```yaml
type: custom:uhrturm-departures-card
entity: sensor.mozartgasse_hbf_departure_1   # the board's first departure sensor
title: Mozartgasse → Hbf                     # optional, default: stop name
hide_cancelled: false                        # optional
line_colors:                                 # optional, override automatic colours
  "1": "#e2001a"
  "58": "#0069b4"
```

One row per departure: a coloured line badge (each line gets its own colour), the
destination and the minutes until departure (real-time). Delayed departures are red
and show the delay (`7 min +2`); cancelled ones are struck through.

## Examples

### Dashboard (markdown card)

```yaml
type: markdown
title: Uni/Mozartgasse → Hbf
content: >
  {% set s = 'sensor.mozartgasse_hbf_departure_1' %}
  | | Linie | Ziel | |
  |---|---|---|---|
  {% for d in state_attr(s, 'departures') or [] -%}
  | {{ as_timestamp(d.time) | timestamp_custom('%H:%M') }}
  | {{ d.line }} | {{ d.destination }}
  | {{ '❌' if d.cancelled else ('+' ~ d.delay if d.delay else '') }} |
  {% endfor %}
```

### Notification: time to leave

```yaml
alias: Commute – leave now
triggers:
  - trigger: template
    value_template: >
      {% set t = states('sensor.home_work_leave_at') %}
      {{ t not in ['unknown', 'unavailable']
         and (as_datetime(t) - now()).total_seconds() <= 300 }}
conditions:
  - condition: state
    entity_id: person.me
    state: home
actions:
  - action: notify.mobile_app_my_phone
    data:
      title: Losgehen!
      message: >
        {% set t = as_datetime(states('sensor.home_work_leave_at')) %}
        In {{ ((t - now()).total_seconds() / 60) | round }} min zur
        {{ states('sensor.home_work_station') }}:
        {{ state_attr('sensor.home_work_leave_at', 'lines') | join(' → ') }},
        an {{ as_timestamp(states('sensor.home_work_arrival')) | timestamp_custom('%H:%M') }}
```

### Actions

```yaml
action: uhrturm_oepnv.get_departures
data:
  config_entry_id: <entry id>
  stop: "at:46:4131"
  towards: Hauptbahnhof
  count: 5
response_variable: result
```

`uhrturm_oepnv.search_stops` returns stop IDs for a name, `uhrturm_oepnv.plan_trip` returns
connections between two stop IDs (or `geo:LAT,LON`).

## Troubleshooting

Download the diagnostics of the integration entry: it contains the last TRIAS request
(API key redacted) and response. Providers differ in details; please attach it to issues.

The scripts in `tools/` are standalone (only `requests` needed) and useful to test an
endpoint outside Home Assistant (`TRIAS_DUMP=1` writes the raw XML).

## Development

```bash
pip install -r requirements_test.txt
pytest
```
