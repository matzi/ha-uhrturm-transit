"""Constants for the Uhrturm Transit integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "uhrturm_oepnv"

# config entry
CONF_REQUESTOR_REF: Final = "requestor_ref"

# subentry types
SUBENTRY_BOARD: Final = "departure_board"
SUBENTRY_COMMUTE: Final = "commute"

# shared subentry keys
CONF_COUNT: Final = "count"
CONF_DELAY_THRESHOLD: Final = "delay_threshold"
CONF_SCAN_INTERVAL: Final = "scan_interval"

# departure board
CONF_STOP_REF: Final = "stop_ref"
CONF_STOP_NAME: Final = "stop_name"
CONF_TOWARDS_QUERY: Final = "towards_query"
CONF_TOWARDS_REF: Final = "towards_ref"
CONF_TOWARDS_NAME: Final = "towards_name"
CONF_LINES: Final = "lines"
CONF_DIRECTION_REF: Final = "direction_ref"
CONF_MIN_MINUTES: Final = "min_minutes"

# commute
CONF_STATIONS: Final = "stations"
CONF_DESTINATION: Final = "destination"
CONF_REF: Final = "ref"
CONF_WALK_MIN: Final = "walk_min"
CONF_WEEKDAYS: Final = "weekdays"
CONF_START_TIME: Final = "start_time"
CONF_END_TIME: Final = "end_time"

WEEKDAYS: Final = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

DEFAULT_COUNT: Final = 5
DEFAULT_DELAY_THRESHOLD: Final = 5
DEFAULT_BOARD_SCAN_INTERVAL: Final = 60  # seconds
DEFAULT_COMMUTE_SCAN_INTERVAL: Final = 2  # minutes
DEFAULT_START_TIME: Final = "06:00:00"
DEFAULT_END_TIME: Final = "09:00:00"
DEFAULT_WEEKDAYS: Final = ["mon", "tue", "wed", "thu", "fri"]

# polling
IDLE_BOARD_INTERVAL: Final = timedelta(minutes=5)  # next departure far away
IDLE_BOARD_THRESHOLD: Final = timedelta(minutes=30)
INACTIVE_COMMUTE_INTERVAL: Final = timedelta(minutes=5)  # outside time window: no API call
STATE_TICK: Final = timedelta(seconds=30)  # drop past departures between polls

# fetch more than shown, because filtering happens client-side
FETCH_FACTOR: Final = 4
FETCH_MIN: Final = 20
FETCH_MAX: Final = 60

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"

# dashboard card shipped with the integration
CARD_URL_PATH: Final = f"/{DOMAIN}"
CARD_FILENAME: Final = "uhrturm-departures-card.js"
