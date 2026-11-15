"""Actions and diagnostics."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component

from custom_components.trias.const import DOMAIN

from .conftest import NOW, setup_entry

pytestmark = pytest.mark.freeze_time(NOW)


async def test_search_stops(hass: HomeAssistant, config_entry, mock_trias) -> None:
    """search_stops returns IDs."""
    await setup_entry(hass, config_entry)
    resp = await hass.services.async_call(
        DOMAIN,
        "search_stops",
        {"config_entry_id": config_entry.entry_id, "query": "Mozart"},
        blocking=True,
        return_response=True,
    )
    assert [s["ref"] for s in resp["stops"]] == ["at:46:4131", "at:46:4132"]


async def test_get_departures(hass: HomeAssistant, config_entry, mock_trias) -> None:
    """get_departures with towards (name) and lines."""
    await setup_entry(hass, config_entry)
    resp = await hass.services.async_call(
        DOMAIN,
        "get_departures",
        {
            "config_entry_id": config_entry.entry_id,
            "stop": "at:46:4131",
            "towards": "Ragnitz",
            "count": 5,
        },
        blocking=True,
        return_response=True,
    )
    assert [d["destination"] for d in resp["departures"]] == ["Ragnitz", "Ragnitz"]
    sent = mock_trias.mock_calls[-1][2].decode()
    assert "<IncludeOnwardCalls>true</IncludeOnwardCalls>" in sent

    resp = await hass.services.async_call(
        DOMAIN,
        "get_departures",
        {"config_entry_id": config_entry.entry_id, "stop": "at:46:4131", "lines": ["63"]},
        blocking=True,
        return_response=True,
    )
    assert [d["line"] for d in resp["departures"]] == ["63"]


async def test_plan_trip(hass: HomeAssistant, config_entry, mock_trias) -> None:
    """plan_trip returns serialized trips."""
    await setup_entry(hass, config_entry)
    resp = await hass.services.async_call(
        DOMAIN,
        "plan_trip",
        {
            "config_entry_id": config_entry.entry_id,
            "origin": "at:46:1002",
            "destination": "at:46:2000",
            "time": "2026-11-16 06:35:00",
        },
        blocking=True,
        return_response=True,
    )
    assert len(resp["trips"]) == 2
    assert resp["trips"][0]["lines"] == ["1", "7"]
    sent = mock_trias.mock_calls[-1][2].decode()
    assert "<DepArrTime>2026-11-16T06:35:00+01:00</DepArrTime>" in sent


async def test_service_bad_entry(hass: HomeAssistant, config_entry, mock_trias) -> None:
    """Unknown entry -> validation error."""
    await setup_entry(hass, config_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "search_stops",
            {"config_entry_id": "nope", "query": "x"},
            blocking=True,
            return_response=True,
        )


async def test_diagnostics(hass: HomeAssistant, hass_client, config_entry, mock_trias) -> None:
    """Diagnostics redact the key and contain the last exchange."""
    assert await async_setup_component(hass, "diagnostics", {})
    await setup_entry(hass, config_entry)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)
    assert diag["entry"]["data"]["requestor_ref"] == "**REDACTED**"
    assert "secret-key" not in str(diag)
    assert "**REDACTED**" in diag["last_request"]
    kinds = {c["type"] for c in diag["coordinators"].values()}
    assert kinds == {"departure_board", "commute"}
    board = next(c for c in diag["coordinators"].values() if c["type"] == "departure_board")
    assert len(board["result"]["departures"]) == 3
