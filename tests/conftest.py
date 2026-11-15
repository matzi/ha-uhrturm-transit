"""Fixtures for TRIAS tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant

from custom_components.trias.const import (
    CONF_COUNT,
    CONF_DELAY_THRESHOLD,
    CONF_DESTINATION,
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
    DOMAIN,
    SUBENTRY_BOARD,
    SUBENTRY_COMMUTE,
)

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://trias.example.org/trias"
NOW = "2026-11-16T06:30:00+01:00"  # Monday


def fixture(name: str) -> str:
    """Load a fixture file."""
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations."""
    return


@pytest.fixture(autouse=True)
async def vienna_time_zone(hass: HomeAssistant) -> None:
    """Run tests in the time zone the fixtures are written for."""
    await hass.config.async_set_time_zone("Europe/Vienna")


@pytest.fixture
def trias_router() -> dict[str, Callable[[str], AiohttpClientMockResponse | str]]:
    """Request body -> response mapping, tests may override entries."""
    return {}


@pytest.fixture
def mock_trias(aioclient_mock: AiohttpClientMocker, trias_router: dict) -> AiohttpClientMocker:
    """Answer TRIAS requests from fixtures, routed by request type."""

    async def side_effect(method, url, data):
        body = data.decode() if isinstance(data, bytes) else data
        if "<LocationInformationRequest>" in body:
            kind = "location"
        elif "<StopEventRequest>" in body:
            kind = "stop_event"
        elif "<TripRequest>" in body:
            kind = "trip"
        else:
            raise AssertionError(body)
        if kind in trias_router:
            result = trias_router[kind](body)
            if isinstance(result, AiohttpClientMockResponse):
                return result
            return AiohttpClientMockResponse(method, url, text=result)
        if kind == "location":
            text = fixture("location.xml")
        elif kind == "stop_event":
            text = fixture("stop_events.xml")
        else:
            origin = re.search(r"<Origin>.*?<StopPointRef>(.*?)</StopPointRef>", body, re.S)
            text = fixture(f"trips_{origin.group(1).replace(':', '_')}.xml")
        return AiohttpClientMockResponse(method, url, text=text)

    aioclient_mock.post(URL, side_effect=side_effect)
    return aioclient_mock


BOARD_DATA = {
    CONF_STOP_REF: "at:46:4131",
    CONF_STOP_NAME: "Uni/Mozartgasse",
    CONF_TOWARDS_QUERY: "Hauptbahnhof",
    CONF_TOWARDS_REF: "at:46:2000",
    CONF_TOWARDS_NAME: "Hauptbahnhof",
    CONF_LINES: [],
    CONF_COUNT: 3,
    CONF_MIN_MINUTES: 0,
    CONF_DELAY_THRESHOLD: 5,
    CONF_SCAN_INTERVAL: 60,
}

COMMUTE_DATA = {
    CONF_STATIONS: [
        {CONF_REF: "at:46:1001", "name": "Station A", CONF_WALK_MIN: 4},
        {CONF_REF: "at:46:1002", "name": "Station B", CONF_WALK_MIN: 7},
        {CONF_REF: "at:46:1003", "name": "Station C", CONF_WALK_MIN: 11},
    ],
    CONF_DESTINATION: {CONF_REF: "at:46:2000", "name": "Work", CONF_WALK_MIN: 3},
    CONF_WEEKDAYS: ["mon", "tue", "wed", "thu", "fri"],
    CONF_START_TIME: "06:00:00",
    CONF_END_TIME: "09:00:00",
    CONF_SCAN_INTERVAL: 2,
    CONF_COUNT: 5,
    CONF_DELAY_THRESHOLD: 5,
}


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Config entry with one board and one commute."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="trias.example.org",
        data={CONF_URL: URL, CONF_REQUESTOR_REF: "secret-key"},
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_BOARD,
                title="Mozartgasse → Hbf",
                data=BOARD_DATA,
                unique_id=None,
            ),
            ConfigSubentryData(
                subentry_type=SUBENTRY_COMMUTE,
                title="Home → Work",
                data=COMMUTE_DATA,
                unique_id=None,
            ),
        ],
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up the entry."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
