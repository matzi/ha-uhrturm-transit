"""Data update coordinators for TRIAS."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
import logging

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    CommuteOption,
    Departure,
    Place,
    TriasApiError,
    TriasAuthError,
    TriasClient,
    TriasError,
    filter_departures,
    plan_commute,
)
from .const import (
    CONF_COUNT,
    CONF_DESTINATION,
    CONF_DIRECTION_REF,
    CONF_END_TIME,
    CONF_LINES,
    CONF_MIN_MINUTES,
    CONF_REF,
    CONF_SCAN_INTERVAL,
    CONF_START_TIME,
    CONF_STATIONS,
    CONF_STOP_REF,
    CONF_TOWARDS_NAME,
    CONF_TOWARDS_REF,
    CONF_WALK_MIN,
    CONF_WEEKDAYS,
    DEFAULT_BOARD_SCAN_INTERVAL,
    DEFAULT_COMMUTE_SCAN_INTERVAL,
    DEFAULT_COUNT,
    DEFAULT_END_TIME,
    DEFAULT_START_TIME,
    DEFAULT_WEEKDAYS,
    DOMAIN,
    FETCH_FACTOR,
    FETCH_MAX,
    FETCH_MIN,
    IDLE_BOARD_INTERVAL,
    IDLE_BOARD_THRESHOLD,
    INACTIVE_COMMUTE_INTERVAL,
    WEEKDAYS,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class TriasRuntimeData:
    """Runtime data of a config entry."""

    client: TriasClient
    coordinators: dict[str, BoardCoordinator | CommuteCoordinator] = field(default_factory=dict)


type TriasConfigEntry = ConfigEntry[TriasRuntimeData]


@dataclass
class BoardData:
    """Result of a departure board update."""

    departures: list[Departure]
    fetched_at: datetime
    messages: list[str] = field(default_factory=list)


@dataclass
class CommuteData:
    """Result of a commute update."""

    active: bool
    options: list[CommuteOption]
    fetched_at: datetime
    messages: list[str] = field(default_factory=list)


def _wrap_errors(err: TriasError) -> Exception:
    if isinstance(err, TriasAuthError):
        return ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="auth_failed")
    return UpdateFailed(
        translation_domain=DOMAIN,
        translation_key="update_failed",
        translation_placeholders={"error": str(err)},
    )


class BoardCoordinator(DataUpdateCoordinator[BoardData]):
    """Fetch departures for one departure board subentry."""

    config_entry: TriasConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: TriasConfigEntry,
        subentry: ConfigSubentry,
        client: TriasClient,
    ) -> None:
        """Initialize."""
        self.subentry = subentry
        self.client = client
        self._base_interval = timedelta(
            seconds=int(subentry.data.get(CONF_SCAN_INTERVAL, DEFAULT_BOARD_SCAN_INTERVAL))
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"TRIAS departures {subentry.title}",
            update_interval=self._base_interval,
        )

    @property
    def count(self) -> int:
        """Number of departures to show."""
        return int(self.subentry.data.get(CONF_COUNT, DEFAULT_COUNT))

    @property
    def min_minutes(self) -> int:
        """Hide departures earlier than now + this many minutes."""
        return int(self.subentry.data.get(CONF_MIN_MINUTES, 0))

    async def _async_update_data(self) -> BoardData:
        data = self.subentry.data
        now = dt_util.now()
        towards_ref = data.get(CONF_TOWARDS_REF)
        towards_name = data.get(CONF_TOWARDS_NAME)
        lines = data.get(CONF_LINES) or []
        direction_ref = data.get(CONF_DIRECTION_REF)
        has_towards = bool(towards_ref or towards_name)
        client_filter = has_towards or bool(lines) or bool(direction_ref)
        fetch = (
            min(max(self.count * FETCH_FACTOR, FETCH_MIN), FETCH_MAX)
            if client_filter
            else max(self.count * 2, 10)
        )
        messages: list[str] = []
        try:
            deps = await self.client.departures(
                data[CONF_STOP_REF],
                now + timedelta(minutes=self.min_minutes),
                fetch,
                onward=has_towards,
            )
            messages = list(self.client.last_warnings)
        except TriasApiError as err:
            # e.g. no departures at night: not an error for us
            deps, messages = [], err.messages
        except TriasError as err:
            raise _wrap_errors(err) from err

        deps = filter_departures(
            deps,
            towards_ref=towards_ref,
            # a stored ref wins; the name is only used for name matching
            towards_name=None if towards_ref else towards_name,
            lines=lines,
            direction_ref=direction_ref,
        )

        # poll less often when nothing leaves soon
        upcoming = [d for d in deps if not d.service.cancelled and d.call.time]
        if not upcoming or upcoming[0].call.time - now > IDLE_BOARD_THRESHOLD:
            self.update_interval = max(self._base_interval, IDLE_BOARD_INTERVAL)
        else:
            self.update_interval = self._base_interval

        return BoardData(departures=deps, fetched_at=now, messages=messages)


def _parse_time(value: str, default: str) -> time:
    try:
        return time.fromisoformat(value)
    except (TypeError, ValueError):
        return time.fromisoformat(default)


class CommuteCoordinator(DataUpdateCoordinator[CommuteData]):
    """Plan home -> work connections for one commute subentry."""

    config_entry: TriasConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: TriasConfigEntry,
        subentry: ConfigSubentry,
        client: TriasClient,
    ) -> None:
        """Initialize."""
        self.subentry = subentry
        self.client = client
        data = subentry.data
        self.stations = [
            Place(ref=s[CONF_REF], name=s["name"], walk_min=int(s.get(CONF_WALK_MIN, 0)))
            for s in data[CONF_STATIONS]
        ]
        dest = data[CONF_DESTINATION]
        self.destination = Place(
            ref=dest[CONF_REF], name=dest["name"], walk_min=int(dest.get(CONF_WALK_MIN, 0))
        )
        self.weekdays = {WEEKDAYS.index(d) for d in data.get(CONF_WEEKDAYS, DEFAULT_WEEKDAYS)}
        self.start = _parse_time(data.get(CONF_START_TIME), DEFAULT_START_TIME)
        self.end = _parse_time(data.get(CONF_END_TIME), DEFAULT_END_TIME)
        self.count = int(data.get(CONF_COUNT, DEFAULT_COUNT))
        self._base_interval = timedelta(
            minutes=int(data.get(CONF_SCAN_INTERVAL, DEFAULT_COMMUTE_SCAN_INTERVAL))
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"TRIAS commute {subentry.title}",
            update_interval=self._base_interval,
        )

    def is_active(self, now: datetime) -> bool:
        """Is `now` inside the configured time window?"""
        t = now.time().replace(tzinfo=None)
        if self.start <= self.end:
            return now.weekday() in self.weekdays and self.start <= t <= self.end
        # window over midnight: the weekday refers to the start day
        if t >= self.start:
            return now.weekday() in self.weekdays
        if t <= self.end:
            return (now.weekday() - 1) % 7 in self.weekdays
        return False

    def _until_active(self, now: datetime) -> timedelta:
        start_today = now.replace(
            hour=self.start.hour, minute=self.start.minute, second=0, microsecond=0
        )
        if now.weekday() in self.weekdays and now < start_today:
            return start_today - now
        return INACTIVE_COMMUTE_INTERVAL

    async def _async_update_data(self) -> CommuteData:
        now = dt_util.now()
        if not self.is_active(now):
            self.update_interval = max(
                min(self._until_active(now), INACTIVE_COMMUTE_INTERVAL), timedelta(seconds=10)
            )
            return CommuteData(active=False, options=[], fetched_at=now)

        self.update_interval = self._base_interval
        messages: list[str] = []
        try:
            options = await plan_commute(
                self.client,
                self.stations,
                self.destination,
                now,
                per_station=max(self.count + 1, 4),
            )
        except TriasApiError as err:
            options, messages = [], err.messages
        except TriasError as err:
            raise _wrap_errors(err) from err
        return CommuteData(active=True, options=options, fetched_at=now, messages=messages)
