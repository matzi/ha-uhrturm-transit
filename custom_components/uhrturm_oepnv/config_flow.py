"""Config flow for TRIAS: endpoint + departure board / commute subentries."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME, CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeSelector,
)

from .api import (
    StopInfo,
    TriasApiError,
    TriasAuthError,
    TriasClient,
    TriasConnectionError,
    TriasError,
    TriasParseError,
)
from .const import (
    CONF_COUNT,
    CONF_DELAY_THRESHOLD,
    CONF_DESTINATION,
    CONF_DIRECTION_REF,
    CONF_END_TIME,
    CONF_LINES,
    CONF_MIN_MINUTES,
    CONF_REF,
    CONF_REQUESTOR_REF,
    CONF_SCAN_INTERVAL,
    CONF_START_TIME,
    CONF_STATIONS,
    CONF_STOP_NAME,
    CONF_STOP_REF,
    CONF_TOWARDS_NAME,
    CONF_TOWARDS_QUERY,
    CONF_TOWARDS_REF,
    CONF_WALK_MIN,
    CONF_WEEKDAYS,
    DEFAULT_BOARD_SCAN_INTERVAL,
    DEFAULT_COMMUTE_SCAN_INTERVAL,
    DEFAULT_COUNT,
    DEFAULT_DELAY_THRESHOLD,
    DEFAULT_END_TIME,
    DEFAULT_START_TIME,
    DEFAULT_WEEKDAYS,
    DOMAIN,
    SUBENTRY_BOARD,
    SUBENTRY_COMMUTE,
    WEEKDAYS,
)

_LOGGER = logging.getLogger(__name__)

CONF_QUERY = "query"
NAME_MATCH = "__name__"
VALIDATION_QUERY = "Bahnhof"


def _number(minimum: int, maximum: int, unit: str | None = None, step: int = 1) -> NumberSelector:
    config = NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=NumberSelectorMode.BOX)
    if unit:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


_TEXT = TextSelector()
_QUERY_SCHEMA = vol.Schema({vol.Required(CONF_QUERY): _TEXT})


async def _validate_endpoint(hass: HomeAssistant, url: str, requestor_ref: str) -> str | None:
    """Return an error key or None."""
    client = TriasClient(async_get_clientsession(hass), url, requestor_ref)
    try:
        await client.search_stops(VALIDATION_QUERY, 1)
    except TriasApiError:
        return None  # the server answered in TRIAS, that's good enough
    except TriasAuthError:
        return "invalid_auth"
    except TriasParseError:
        return "invalid_response"
    except TriasConnectionError:
        return "cannot_connect"
    except Exception:
        _LOGGER.exception("Unexpected error validating TRIAS endpoint")
        return "unknown"
    return None


def _endpoint_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=defaults.get(CONF_URL, vol.UNDEFINED)): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            ),
            vol.Optional(
                CONF_REQUESTOR_REF, default=defaults.get(CONF_REQUESTOR_REF, "")
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        }
    )


class TriasConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a TRIAS endpoint."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for URL and requestor ref."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip()
            ref = user_input.get(CONF_REQUESTOR_REF, "").strip()
            self._async_abort_entries_match({CONF_URL: url, CONF_REQUESTOR_REF: ref})
            if (error := await _validate_endpoint(self.hass, url, ref)) is None:
                return self.async_create_entry(
                    title=urlparse(url).hostname or url,
                    data={CONF_URL: url, CONF_REQUESTOR_REF: ref},
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="user", data_schema=_endpoint_schema(user_input), errors=errors
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Credentials were rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new requestor ref."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            ref = user_input.get(CONF_REQUESTOR_REF, "").strip()
            if (error := await _validate_endpoint(self.hass, entry.data[CONF_URL], ref)) is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_REQUESTOR_REF: ref}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_REQUESTOR_REF, default=""): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change URL / requestor ref."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip()
            ref = user_input.get(CONF_REQUESTOR_REF, "").strip()
            if (error := await _validate_endpoint(self.hass, url, ref)) is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_URL: url, CONF_REQUESTOR_REF: ref}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_endpoint_schema(user_input or entry.data),
            errors=errors,
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Departure boards and commutes."""
        return {SUBENTRY_BOARD: BoardSubentryFlow, SUBENTRY_COMMUTE: CommuteSubentryFlow}


# --------------------------------------------------------------------------- shared


class _TriasSubentryFlow(ConfigSubentryFlow):
    """Helpers for stop search in subentry flows."""

    def _client(self) -> TriasClient:
        return self._get_entry().runtime_data.client

    def _not_loaded(self) -> SubentryFlowResult | None:
        if self._get_entry().state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")
        return None

    async def _search(self, query: str) -> tuple[list[StopInfo], str | None]:
        try:
            stops = await self._client().search_stops(query.strip(), 15)
        except TriasApiError:
            stops = []
        except TriasError:
            return [], "cannot_connect"
        if not stops:
            return [], "no_stops_found"
        return stops, None

    @staticmethod
    def _stop_selector(stops: list[StopInfo], extra: list[SelectOptionDict] | None = None):
        options = [SelectOptionDict(value=s.ref, label=f"{s.label} [{s.ref}]") for s in stops]
        return SelectSelector(
            SelectSelectorConfig(options=options + (extra or []), mode=SelectSelectorMode.DROPDOWN)
        )


# --------------------------------------------------------------------------- departure board


class BoardSubentryFlow(_TriasSubentryFlow):
    """Add or reconfigure a departure board."""

    def __init__(self) -> None:
        """Initialize."""
        self._stops: dict[str, StopInfo] = {}
        self._towards: dict[str, StopInfo] = {}
        self._towards_query = ""
        self._data: dict[str, Any] = {}
        self._title = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Search the stop."""
        if abort := self._not_loaded():
            return abort
        errors: dict[str, str] = {}
        if user_input is not None:
            stops, error = await self._search(user_input[CONF_QUERY])
            if error:
                errors[CONF_QUERY] = error
            else:
                self._stops = {s.ref: s for s in stops}
                return await self.async_step_select_stop()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(_QUERY_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_select_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick the stop from the search results."""
        if user_input is not None:
            stop = self._stops[user_input[CONF_STOP_REF]]
            self._data = {CONF_STOP_REF: stop.ref, CONF_STOP_NAME: stop.name or stop.ref}
            self._title = stop.name or stop.ref
            return await self.async_step_filters()
        return self.async_show_form(
            step_id="select_stop",
            data_schema=vol.Schema(
                {vol.Required(CONF_STOP_REF): self._stop_selector(list(self._stops.values()))}
            ),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit the filters of an existing board."""
        subentry = self._get_reconfigure_subentry()
        self._data = dict(subentry.data)
        self._title = subentry.title
        return await self.async_step_filters()

    def _filters_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_NAME): _TEXT,
                vol.Optional(CONF_TOWARDS_QUERY): _TEXT,
                vol.Optional(CONF_LINES): _TEXT,
                vol.Optional(CONF_DIRECTION_REF): _TEXT,
                vol.Required(CONF_COUNT): _number(1, 20),
                vol.Required(CONF_MIN_MINUTES): _number(0, 60, "min"),
                vol.Required(CONF_DELAY_THRESHOLD): _number(1, 60, "min"),
                vol.Required(CONF_SCAN_INTERVAL): _number(30, 900, "s", 10),
            }
        )

    async def async_step_filters(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Name, direction and display options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._title = user_input[CONF_NAME].strip() or self._data[CONF_STOP_NAME]
            lines = [x.strip() for x in user_input.get(CONF_LINES, "").split(",") if x.strip()]
            previous_query = self._data.get(CONF_TOWARDS_QUERY, "")
            query = user_input.get(CONF_TOWARDS_QUERY, "").strip()
            self._data.update(
                {
                    CONF_LINES: lines,
                    CONF_DIRECTION_REF: user_input.get(CONF_DIRECTION_REF, "").strip() or None,
                    CONF_COUNT: int(user_input[CONF_COUNT]),
                    CONF_MIN_MINUTES: int(user_input[CONF_MIN_MINUTES]),
                    CONF_DELAY_THRESHOLD: int(user_input[CONF_DELAY_THRESHOLD]),
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                }
            )
            if not query:
                self._data.update(
                    {CONF_TOWARDS_QUERY: "", CONF_TOWARDS_REF: None, CONF_TOWARDS_NAME: None}
                )
                return self._finish()
            if query == previous_query:
                return self._finish()
            stops, error = await self._search(query)
            if error == "cannot_connect":
                errors[CONF_TOWARDS_QUERY] = error
            else:
                self._towards = {s.ref: s for s in stops}
                self._towards_query = query
                return await self.async_step_select_towards()

        suggested = {
            CONF_NAME: self._title,
            CONF_TOWARDS_QUERY: self._data.get(CONF_TOWARDS_QUERY) or "",
            CONF_LINES: ", ".join(self._data.get(CONF_LINES) or []),
            CONF_DIRECTION_REF: self._data.get(CONF_DIRECTION_REF) or "",
            CONF_COUNT: self._data.get(CONF_COUNT, DEFAULT_COUNT),
            CONF_MIN_MINUTES: self._data.get(CONF_MIN_MINUTES, 0),
            CONF_DELAY_THRESHOLD: self._data.get(CONF_DELAY_THRESHOLD, DEFAULT_DELAY_THRESHOLD),
            CONF_SCAN_INTERVAL: self._data.get(CONF_SCAN_INTERVAL, DEFAULT_BOARD_SCAN_INTERVAL),
        }
        return self.async_show_form(
            step_id="filters",
            data_schema=self.add_suggested_values_to_schema(
                self._filters_schema(), user_input or suggested
            ),
            description_placeholders={"stop": self._data.get(CONF_STOP_NAME, "")},
            errors=errors,
        )

    async def async_step_select_towards(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick the direction stop (or match by name)."""
        if user_input is not None:
            choice = user_input[CONF_TOWARDS_REF]
            self._data[CONF_TOWARDS_QUERY] = self._towards_query
            if choice == NAME_MATCH:
                self._data[CONF_TOWARDS_REF] = None
                self._data[CONF_TOWARDS_NAME] = self._towards_query
            else:
                stop = self._towards[choice]
                self._data[CONF_TOWARDS_REF] = stop.ref
                self._data[CONF_TOWARDS_NAME] = stop.name or stop.ref
            return self._finish()
        name_option = SelectOptionDict(value=NAME_MATCH, label=f"„{self._towards_query}“ (*)")
        return self.async_show_form(
            step_id="select_towards",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TOWARDS_REF): self._stop_selector(
                        list(self._towards.values()), [name_option]
                    )
                }
            ),
            description_placeholders={"query": self._towards_query},
        )

    def _finish(self) -> SubentryFlowResult:
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=self._title,
                data=self._data,
            )
        return self.async_create_entry(title=self._title, data=self._data)


# --------------------------------------------------------------------------- commute


class CommuteSubentryFlow(_TriasSubentryFlow):
    """Add or reconfigure a commute (several home stations -> one destination)."""

    def __init__(self) -> None:
        """Initialize."""
        self._stops: dict[str, StopInfo] = {}
        self._stations: list[dict[str, Any]] = []
        self._destination: dict[str, Any] | None = None
        self._data: dict[str, Any] = {}
        self._title = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Start with the first home station."""
        if abort := self._not_loaded():
            return abort
        return await self.async_step_station_search()

    async def async_step_station_search(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Search a home station."""
        errors: dict[str, str] = {}
        if user_input is not None:
            stops, error = await self._search(user_input[CONF_QUERY])
            if error:
                errors[CONF_QUERY] = error
            else:
                self._stops = {s.ref: s for s in stops}
                return await self.async_step_station_select()
        return self.async_show_form(
            step_id="station_search",
            data_schema=self.add_suggested_values_to_schema(_QUERY_SCHEMA, user_input),
            description_placeholders={"stations": self._station_list()},
            errors=errors,
        )

    async def async_step_station_select(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick the station and enter the walking time."""
        if user_input is not None:
            stop = self._stops[user_input[CONF_REF]]
            walk = int(user_input[CONF_WALK_MIN])
            existing = next((s for s in self._stations if s[CONF_REF] == stop.ref), None)
            if existing is not None:
                existing[CONF_WALK_MIN] = walk  # picked again: just update the walk time
            else:
                self._stations.append(
                    {CONF_REF: stop.ref, "name": stop.name or stop.ref, CONF_WALK_MIN: walk}
                )
            return await self.async_step_station_menu()
        return self.async_show_form(
            step_id="station_select",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REF): self._stop_selector(list(self._stops.values())),
                    vol.Required(CONF_WALK_MIN, default=5): _number(0, 60, "min"),
                }
            ),
        )

    async def async_step_station_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add another station or continue with the destination."""
        return self.async_show_menu(
            step_id="station_menu",
            menu_options=["station_search", "destination_search"],
            description_placeholders={"stations": self._station_list()},
        )

    async def async_step_destination_search(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Search the destination stop."""
        errors: dict[str, str] = {}
        if user_input is not None:
            stops, error = await self._search(user_input[CONF_QUERY])
            if error:
                errors[CONF_QUERY] = error
            else:
                self._stops = {s.ref: s for s in stops}
                return await self.async_step_destination_select()
        return self.async_show_form(
            step_id="destination_search",
            data_schema=self.add_suggested_values_to_schema(_QUERY_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_destination_select(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick the destination and the walking time from it."""
        if user_input is not None:
            stop = self._stops[user_input[CONF_REF]]
            self._destination = {
                CONF_REF: stop.ref,
                "name": stop.name or stop.ref,
                CONF_WALK_MIN: int(user_input[CONF_WALK_MIN]),
            }
            self._title = f"{self._stations[0]['name']} → {self._destination['name']}"
            return await self.async_step_settings()
        return self.async_show_form(
            step_id="destination_select",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REF): self._stop_selector(list(self._stops.values())),
                    vol.Required(CONF_WALK_MIN, default=3): _number(0, 60, "min"),
                }
            ),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit time window and display options."""
        subentry = self._get_reconfigure_subentry()
        self._data = dict(subentry.data)
        self._stations = list(self._data[CONF_STATIONS])
        self._destination = self._data[CONF_DESTINATION]
        self._title = subentry.title
        return await self.async_step_settings()

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Name, active time window, polling and display options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_WEEKDAYS):
                errors[CONF_WEEKDAYS] = "no_weekdays"
            elif user_input[CONF_START_TIME] == user_input[CONF_END_TIME]:
                errors[CONF_END_TIME] = "empty_window"
            else:
                self._title = user_input[CONF_NAME].strip() or self._title
                data = {
                    CONF_STATIONS: self._stations,
                    CONF_DESTINATION: self._destination,
                    CONF_WEEKDAYS: [d for d in WEEKDAYS if d in user_input[CONF_WEEKDAYS]],
                    CONF_START_TIME: user_input[CONF_START_TIME],
                    CONF_END_TIME: user_input[CONF_END_TIME],
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_COUNT: int(user_input[CONF_COUNT]),
                    CONF_DELAY_THRESHOLD: int(user_input[CONF_DELAY_THRESHOLD]),
                }
                if self.source == SOURCE_RECONFIGURE:
                    return self.async_update_and_abort(
                        self._get_entry(),
                        self._get_reconfigure_subentry(),
                        title=self._title,
                        data=data,
                    )
                return self.async_create_entry(title=self._title, data=data)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): _TEXT,
                vol.Required(CONF_WEEKDAYS): SelectSelector(
                    SelectSelectorConfig(
                        options=WEEKDAYS,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                        translation_key="weekdays",
                    )
                ),
                vol.Required(CONF_START_TIME): TimeSelector(),
                vol.Required(CONF_END_TIME): TimeSelector(),
                vol.Required(CONF_SCAN_INTERVAL): _number(1, 30, "min"),
                vol.Required(CONF_COUNT): _number(1, 10),
                vol.Required(CONF_DELAY_THRESHOLD): _number(1, 60, "min"),
            }
        )
        suggested = {
            CONF_NAME: self._title,
            CONF_WEEKDAYS: self._data.get(CONF_WEEKDAYS, DEFAULT_WEEKDAYS),
            CONF_START_TIME: self._data.get(CONF_START_TIME, DEFAULT_START_TIME),
            CONF_END_TIME: self._data.get(CONF_END_TIME, DEFAULT_END_TIME),
            CONF_SCAN_INTERVAL: self._data.get(CONF_SCAN_INTERVAL, DEFAULT_COMMUTE_SCAN_INTERVAL),
            CONF_COUNT: self._data.get(CONF_COUNT, DEFAULT_COUNT),
            CONF_DELAY_THRESHOLD: self._data.get(CONF_DELAY_THRESHOLD, DEFAULT_DELAY_THRESHOLD),
        }
        return self.async_show_form(
            step_id="settings",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or suggested),
            description_placeholders={
                "stations": self._station_list(),
                "destination": (self._destination or {}).get("name", ""),
            },
            errors=errors,
        )

    def _station_list(self) -> str:
        if not self._stations:
            return "–"
        return ", ".join(f"{s['name']} ({s[CONF_WALK_MIN]} min)" for s in self._stations)
