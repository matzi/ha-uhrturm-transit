"""Config flow and subentry flow tests."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse
from yarl import URL as YURL

from homeassistant import config_entries
from homeassistant.const import CONF_NAME, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.uhrturm_oepnv.const import (
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

from .conftest import NOW, URL, setup_entry

pytestmark = pytest.mark.freeze_time(NOW)


async def test_user_flow(hass: HomeAssistant, mock_trias) -> None:
    """Create the endpoint entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: URL, CONF_REQUESTOR_REF: "key"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "trias.example.org"
    assert result["data"] == {CONF_URL: URL, CONF_REQUESTOR_REF: "key"}
    sent = mock_trias.mock_calls[-1][2].decode()
    assert "<siri:RequestorRef>key</siri:RequestorRef>" in sent


@pytest.mark.parametrize(
    ("response", "error"),
    [
        ({"status": HTTPStatus.FORBIDDEN}, "invalid_auth"),
        ({"status": HTTPStatus.NOT_FOUND, "text": "nope"}, "cannot_connect"),
        ({"text": "<html>not trias</html>"}, "invalid_response"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, mock_trias, trias_router, response, error
) -> None:
    """Errors are shown and the flow can recover."""
    trias_router["location"] = lambda body: AiohttpClientMockResponse("post", YURL(URL), **response)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: URL, CONF_REQUESTOR_REF: "key"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    del trias_router["location"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: URL, CONF_REQUESTOR_REF: "key"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_duplicate(hass: HomeAssistant, config_entry, mock_trias) -> None:
    """Same URL and key -> abort."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: URL, CONF_REQUESTOR_REF: "secret-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, config_entry, mock_trias) -> None:
    """New key via reauth."""
    await setup_entry(hass, config_entry)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REQUESTOR_REF: "new-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_REQUESTOR_REF] == "new-key"


async def test_reconfigure(hass: HomeAssistant, config_entry, mock_trias) -> None:
    """Change URL."""
    await setup_entry(hass, config_entry)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: URL, CONF_REQUESTOR_REF: "other"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_REQUESTOR_REF] == "other"


async def _start_subflow(hass: HomeAssistant, entry, subentry_type: str):
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, subentry_type), context={"source": config_entries.SOURCE_USER}
    )


async def test_board_subentry(hass: HomeAssistant, config_entry, mock_trias) -> None:
    """Add a departure board: search, select, filters, direction."""
    await setup_entry(hass, config_entry)
    result = await _start_subflow(hass, config_entry, SUBENTRY_BOARD)
    assert result["step_id"] == "user"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"query": "Mozartgasse"}
    )
    assert result["step_id"] == "select_stop"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_STOP_REF: "at:46:4131"}
    )
    assert result["step_id"] == "filters"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Zum Hbf",
            CONF_TOWARDS_QUERY: "Mozartgasse",
            CONF_LINES: "58, 63",
            CONF_COUNT: 4,
            CONF_MIN_MINUTES: 2,
            CONF_DELAY_THRESHOLD: 5,
            CONF_SCAN_INTERVAL: 60,
        },
    )
    assert result["step_id"] == "select_towards"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_TOWARDS_REF: "at:46:4132"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    sub = next(s for s in config_entry.subentries.values() if s.title == "Zum Hbf")
    assert sub.data[CONF_STOP_REF] == "at:46:4131"
    assert sub.data[CONF_LINES] == ["58", "63"]
    assert sub.data[CONF_TOWARDS_REF] == "at:46:4132"
    assert sub.data[CONF_TOWARDS_NAME] == "Mozartgasse"
    assert sub.data[CONF_COUNT] == 4

    # reconfigure: direction by name match, no lines
    result = await config_entry.start_subentry_reconfigure_flow(hass, sub.subentry_id)
    assert result["step_id"] == "filters"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Zum Hbf",
            CONF_TOWARDS_QUERY: "Hauptbahnhof",
            CONF_COUNT: 4,
            CONF_MIN_MINUTES: 0,
            CONF_DELAY_THRESHOLD: 5,
            CONF_SCAN_INTERVAL: 60,
        },
    )
    assert result["step_id"] == "select_towards"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_TOWARDS_REF: "__name__"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    sub = config_entry.subentries[sub.subentry_id]
    assert sub.data[CONF_TOWARDS_REF] is None
    assert sub.data[CONF_TOWARDS_NAME] == "Hauptbahnhof"
    assert sub.data[CONF_LINES] == []


async def test_board_subentry_no_results(
    hass: HomeAssistant, config_entry, mock_trias, trias_router
) -> None:
    """Empty search -> field error."""
    await setup_entry(hass, config_entry)
    from .conftest import fixture

    trias_router["location"] = lambda body: fixture("error_no_results.xml").replace(
        "StopEventResponse", "LocationInformationResponse"
    )
    result = await _start_subflow(hass, config_entry, SUBENTRY_BOARD)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"query": "xyz"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"query": "no_stops_found"}


async def test_commute_subentry(hass: HomeAssistant, config_entry, mock_trias) -> None:
    """Add a commute with two home stations."""
    await setup_entry(hass, config_entry)
    result = await _start_subflow(hass, config_entry, SUBENTRY_COMMUTE)
    assert result["step_id"] == "station_search"

    for ref, walk in (("at:46:4131", 4), ("at:46:4132", 8)):
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"query": "Mozartgasse"}
        )
        assert result["step_id"] == "station_select"
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {CONF_REF: ref, CONF_WALK_MIN: walk}
        )
        assert result["type"] is FlowResultType.MENU
        if ref == "at:46:4131":
            result = await hass.config_entries.subentries.async_configure(
                result["flow_id"], {"next_step_id": "station_search"}
            )

    # picking a station again updates its walking time instead of duplicating it
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "station_search"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"query": "Mozartgasse"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_REF: "at:46:4131", CONF_WALK_MIN: 1}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["description_placeholders"]["stations"] == (
        "Uni/Mozartgasse (1 min), Mozartgasse (8 min)"
    )


async def test_commute_subentry_full(hass: HomeAssistant, config_entry, mock_trias) -> None:
    """Station -> destination -> settings -> entry; then reconfigure."""
    await setup_entry(hass, config_entry)
    result = await _start_subflow(hass, config_entry, SUBENTRY_COMMUTE)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"query": "Mozartgasse"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_REF: "at:46:4131", CONF_WALK_MIN: 4}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "destination_search"}
    )
    assert result["step_id"] == "destination_search"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"query": "Mozartgasse"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_REF: "at:46:4132", CONF_WALK_MIN: 2}
    )
    assert result["step_id"] == "settings"

    settings = {
        CONF_NAME: "Arbeit",
        CONF_WEEKDAYS: [],
        CONF_START_TIME: "06:00:00",
        CONF_END_TIME: "08:30:00",
        CONF_SCAN_INTERVAL: 2,
        CONF_COUNT: 3,
        CONF_DELAY_THRESHOLD: 4,
    }
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], settings)
    assert result["errors"] == {CONF_WEEKDAYS: "no_weekdays"}

    settings[CONF_WEEKDAYS] = ["fri", "mon"]
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], settings)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    sub = next(s for s in config_entry.subentries.values() if s.title == "Arbeit")
    assert sub.data[CONF_WEEKDAYS] == ["mon", "fri"]
    assert sub.data[CONF_STATIONS] == [
        {CONF_REF: "at:46:4131", "name": "Uni/Mozartgasse", CONF_WALK_MIN: 4}
    ]
    assert sub.data[CONF_DESTINATION][CONF_REF] == "at:46:4132"

    result = await config_entry.start_subentry_reconfigure_flow(hass, sub.subentry_id)
    assert result["step_id"] == "settings"
    settings[CONF_END_TIME] = "09:30:00"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], settings)
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    sub = config_entry.subentries[sub.subentry_id]
    assert sub.data[CONF_END_TIME] == "09:30:00"
    assert sub.data[CONF_STATIONS][0][CONF_REF] == "at:46:4131"


async def test_subentry_requires_loaded_entry(hass: HomeAssistant, config_entry) -> None:
    """Subentries can only be added to a loaded entry."""
    config_entry.add_to_hass(hass)
    result = await _start_subflow(hass, config_entry, SUBENTRY_BOARD)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "entry_not_loaded"
