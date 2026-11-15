"""Async TRIAS client."""

from __future__ import annotations

from datetime import datetime, UTC
import logging
import re

import aiohttp

from . import parser
from .exceptions import TriasAuthError, TriasConnectionError
from .models import Departure, StopInfo, Trip

_LOGGER = logging.getLogger(__name__)
DEFAULT_TIMEOUT = 20


class TriasClient:
    """Minimal async TRIAS 1.2 client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        requestor_ref: str = "",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize."""
        self._session = session
        self._url = url
        self._requestor_ref = requestor_ref
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self.last_request: str | None = None
        self.last_response: str | None = None
        self.last_warnings: list[str] = []

    async def _post(self, payload: str, result_tag: str):
        now = datetime.now(UTC).astimezone()
        xml = parser.build_envelope(payload, self._requestor_ref, now)
        # keep a redacted copy for diagnostics
        self.last_request = re.sub(
            r"(<siri:RequestorRef>).*?(</siri:RequestorRef>)", r"\1**REDACTED**\2", xml
        )
        try:
            async with self._session.post(
                self._url,
                data=xml.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8"},
                timeout=self._timeout,
            ) as resp:
                content = await resp.read()
                status = resp.status
        except (TimeoutError, aiohttp.ClientError) as err:
            raise TriasConnectionError(f"{type(err).__name__}: {err}") from err

        self.last_response = content.decode("utf-8", errors="replace")
        if status in (401, 403):
            raise TriasAuthError(f"HTTP {status}")
        if status != 200:
            raise TriasConnectionError(f"HTTP {status}: {self.last_response[:300]}")

        root, warnings = parser.parse_root(content, result_tag)
        self.last_warnings = warnings
        for warning in warnings:
            _LOGGER.debug("TRIAS warning: %s", warning)
        return root

    async def search_stops(self, name: str, count: int = 10) -> list[StopInfo]:
        """Find stops by name."""
        root = await self._post(parser.build_location_request(name, count), "LocationResult")
        return parser.parse_locations(root)

    async def departures(
        self,
        stop_ref: str,
        when: datetime,
        count: int = 20,
        *,
        onward: bool = False,
        line_filter: list[tuple[str, str | None]] | None = None,
    ) -> list[Departure]:
        """Next departures at a stop."""
        payload = parser.build_stop_event_request(
            stop_ref, when, count, onward=onward, line_filter=line_filter
        )
        root = await self._post(payload, "StopEventResult")
        return parser.parse_stop_events(root)

    async def trips(
        self, origin: str, destination: str, when: datetime, count: int = 5
    ) -> list[Trip]:
        """Plan trips."""
        payload = parser.build_trip_request(origin, destination, when, count)
        root = await self._post(payload, "TripResult")
        return parser.parse_trips(root)
