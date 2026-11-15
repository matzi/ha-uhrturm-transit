"""Actions (services) for ad-hoc TRIAS queries."""

from __future__ import annotations

from datetime import datetime

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .api import TriasApiError, TriasClient, TriasError, filter_departures
from .const import ATTR_CONFIG_ENTRY_ID, DOMAIN, FETCH_MAX

SERVICE_SEARCH_STOPS = "search_stops"
SERVICE_GET_DEPARTURES = "get_departures"
SERVICE_PLAN_TRIP = "plan_trip"

ATTR_QUERY = "query"
ATTR_STOP = "stop"
ATTR_TOWARDS = "towards"
ATTR_LINES = "lines"
ATTR_COUNT = "count"
ATTR_ORIGIN = "origin"
ATTR_DESTINATION = "destination"
ATTR_TIME = "time"

_ENTRY = {vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string}

SEARCH_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Required(ATTR_QUERY): cv.string,
        vol.Optional(ATTR_COUNT, default=10): vol.All(vol.Coerce(int), vol.Range(1, 50)),
    }
)
DEPARTURES_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Required(ATTR_STOP): cv.string,
        vol.Optional(ATTR_TOWARDS): cv.string,
        vol.Optional(ATTR_LINES): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_COUNT, default=10): vol.All(vol.Coerce(int), vol.Range(1, 50)),
        vol.Optional(ATTR_TIME): cv.datetime,
    }
)
TRIP_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Required(ATTR_ORIGIN): cv.string,
        vol.Required(ATTR_DESTINATION): cv.string,
        vol.Optional(ATTR_TIME): cv.datetime,
        vol.Optional(ATTR_COUNT, default=5): vol.All(vol.Coerce(int), vol.Range(1, 20)),
    }
)


def _client(hass: HomeAssistant, call: ServiceCall) -> TriasClient:
    entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
            translation_placeholders={"entry_id": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"title": entry.title},
        )
    return entry.runtime_data.client


def _when(call: ServiceCall) -> datetime:
    value: datetime | None = call.data.get(ATTR_TIME)
    if value is None:
        return dt_util.now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_util.get_default_time_zone())
    return value


def _raise(err: TriasError) -> None:
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="request_failed",
        translation_placeholders={"error": str(err)},
    ) from err


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions."""

    async def search_stops(call: ServiceCall) -> ServiceResponse:
        client = _client(hass, call)
        try:
            stops = await client.search_stops(call.data[ATTR_QUERY], call.data[ATTR_COUNT])
        except TriasApiError:
            stops = []
        except TriasError as err:
            _raise(err)
        return {"stops": [s.as_dict() for s in stops]}

    async def get_departures(call: ServiceCall) -> ServiceResponse:
        client = _client(hass, call)
        towards: str | None = call.data.get(ATTR_TOWARDS)
        lines: list[str] = call.data.get(ATTR_LINES, [])
        count: int = call.data[ATTR_COUNT]
        filtering = bool(towards or lines)
        try:
            deps = await client.departures(
                call.data[ATTR_STOP],
                _when(call),
                min(count * 4, FETCH_MAX) if filtering else count,
                onward=bool(towards),
            )
        except TriasApiError:
            deps = []
        except TriasError as err:
            _raise(err)
        is_ref = bool(towards and ":" in towards)
        deps = filter_departures(
            deps,
            towards_ref=towards if is_ref else None,
            towards_name=None if is_ref else towards,
            lines=lines,
        )
        return {"departures": [d.as_dict() for d in deps[:count]]}

    async def plan_trip(call: ServiceCall) -> ServiceResponse:
        client = _client(hass, call)
        try:
            trips = await client.trips(
                call.data[ATTR_ORIGIN],
                call.data[ATTR_DESTINATION],
                _when(call),
                call.data[ATTR_COUNT],
            )
        except TriasApiError:
            trips = []
        except TriasError as err:
            _raise(err)
        return {"trips": [t.as_dict() for t in trips]}

    hass.services.async_register(
        DOMAIN, SERVICE_SEARCH_STOPS, search_stops, SEARCH_SCHEMA, SupportsResponse.ONLY
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GET_DEPARTURES, get_departures, DEPARTURES_SCHEMA, SupportsResponse.ONLY
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PLAN_TRIP, plan_trip, TRIP_SCHEMA, SupportsResponse.ONLY
    )
