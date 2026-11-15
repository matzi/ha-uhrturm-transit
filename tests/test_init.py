"""Setup, entities and coordinator behaviour."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse
from yarl import URL as YURL

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.uhrturm_oepnv.const import DOMAIN

from .conftest import NOW, URL, fixture, setup_entry


def _entity_id(hass: HomeAssistant, entry, subentry_type: str, key: str) -> str:
    sub = next(s for s in entry.subentries.values() if s.subentry_type == subentry_type)
    ent_reg = er.async_get(hass)
    platform = "binary_sensor" if key == "disruption" else "sensor"
    entity_id = ent_reg.async_get_entity_id(platform, DOMAIN, f"{sub.subentry_id}_{key}")
    assert entity_id, key
    return entity_id


@pytest.mark.freeze_time(NOW)
async def test_setup_and_board(hass: HomeAssistant, config_entry, mock_trias) -> None:
    """Board shows only vehicles towards Hauptbahnhof; cancelled ones are skipped."""
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    nxt = hass.states.get(_entity_id(hass, config_entry, "departure_board", "departure_1"))
    assert nxt.state == "2026-11-16T05:33:00+00:00"  # 06:32 +1
    assert nxt.attributes["line"] == "58"
    assert nxt.attributes["destination"] == "Hauptbahnhof"
    assert nxt.attributes["delay"] == 1
    assert nxt.attributes["platform"] == "1"
    deps = nxt.attributes["departures"]
    assert [d["destination"] for d in deps] == ["Hauptbahnhof"] * 3
    assert deps[1]["cancelled"] is True

    following = hass.states.get(_entity_id(hass, config_entry, "departure_board", "departure_2"))
    assert following.state == "2026-11-16T06:08:00+00:00"  # 07:02 +6, cancelled one skipped
    assert following.attributes["index"] == 2
    assert following.attributes["friendly_name"] == "Mozartgasse → Hbf Departure 2"
    assert "departures" not in following.attributes

    # one sensor per configured departure (count = 3)
    sub = next(s for s in config_entry.subentries.values() if s.subentry_type == "departure_board")
    ent_reg = er.async_get(hass)
    numbered = sorted(
        e.unique_id.removeprefix(f"{sub.subentry_id}_")
        for e in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)
        if e.unique_id.startswith(f"{sub.subentry_id}_departure_")
    )
    assert numbered == ["departure_1", "departure_2", "departure_3"]

    disruption = hass.states.get(_entity_id(hass, config_entry, "departure_board", "disruption"))
    assert disruption.state == STATE_ON
    assert len(disruption.attributes["affected"]) == 2  # cancelled + 6 min delay

    # one device per subentry
    dev_reg = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(dev_reg, config_entry.entry_id)
    assert {d.name for d in devices} == {"Mozartgasse → Hbf", "Home → Work"}


@pytest.mark.freeze_time(NOW)
async def test_commute(hass: HomeAssistant, config_entry, mock_trias) -> None:
    """Commute picks the station and leave time."""
    await setup_entry(hass, config_entry)

    leave = hass.states.get(_entity_id(hass, config_entry, "commute", "leave_at"))
    assert leave.state == "2026-11-16T05:35:00+00:00"  # 06:35
    assert leave.attributes["station"] == "Station B"
    assert leave.attributes["lines"] == ["1", "7"]
    assert leave.attributes["changes"] == 1
    assert len(leave.attributes["connections"]) == 3

    station = hass.states.get(_entity_id(hass, config_entry, "commute", "station"))
    assert station.state == "Station B"
    assert station.attributes["options"] == ["Station A", "Station B", "Station C"]

    arrival = hass.states.get(_entity_id(hass, config_entry, "commute", "arrival"))
    assert arrival.state == "2026-11-16T06:08:00+00:00"  # 07:05 + 3 min walk

    assert (
        hass.states.get(_entity_id(hass, config_entry, "commute", "disruption")).state == STATE_OFF
    )


async def test_commute_outside_window(
    hass: HomeAssistant, config_entry, mock_trias, freezer: FrozenDateTimeFactory
) -> None:
    """Outside the time window no trip request is sent and sensors are unknown."""
    freezer.move_to("2026-11-15T12:00:00+01:00")  # Sunday
    await setup_entry(hass, config_entry)
    trip_calls = [c for c in mock_trias.mock_calls if b"<TripRequest>" in c[2]]
    assert trip_calls == []
    leave = hass.states.get(_entity_id(hass, config_entry, "commute", "leave_at"))
    assert leave.state == STATE_UNKNOWN
    assert leave.attributes["active"] is False
    assert (
        hass.states.get(_entity_id(hass, config_entry, "commute", "disruption")).state
        == STATE_UNKNOWN
    )


async def test_past_departures_drop_without_poll(
    hass: HomeAssistant, config_entry, mock_trias, freezer: FrozenDateTimeFactory
) -> None:
    """The state tick removes departed vehicles between polls."""
    freezer.move_to(NOW)
    await setup_entry(hass, config_entry)
    entity_id = _entity_id(hass, config_entry, "departure_board", "departure_1")
    assert hass.states.get(entity_id).state == "2026-11-16T05:33:00+00:00"

    calls = mock_trias.call_count
    freezer.tick(timedelta(minutes=4, seconds=30))  # 06:34:30, the 06:33 bus is gone
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    # polls may have happened, but the fixture data is identical -> next is now 07:08
    assert mock_trias.call_count >= calls
    assert hass.states.get(entity_id).state == "2026-11-16T06:08:00+00:00"


@pytest.mark.freeze_time(NOW)
async def test_no_departures_is_not_an_error(
    hass: HomeAssistant, config_entry, mock_trias, trias_router
) -> None:
    """TRIAS 'no events' error keeps the entities available."""
    trias_router["stop_event"] = lambda body: fixture("error_no_results.xml")
    await setup_entry(hass, config_entry)
    state = hass.states.get(_entity_id(hass, config_entry, "departure_board", "departure_1"))
    assert state.state == STATE_UNKNOWN


@pytest.mark.freeze_time(NOW)
async def test_connection_error_retries(
    hass: HomeAssistant, config_entry, mock_trias, trias_router
) -> None:
    """HTTP errors on first refresh -> setup retry."""
    trias_router["stop_event"] = lambda body: AiohttpClientMockResponse(
        "post", YURL(URL), status=HTTPStatus.INTERNAL_SERVER_ERROR, text="boom"
    )
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.freeze_time(NOW)
async def test_auth_error_starts_reauth(
    hass: HomeAssistant, config_entry, mock_trias, trias_router
) -> None:
    """HTTP 403 -> reauth flow."""
    trias_router["stop_event"] = lambda body: AiohttpClientMockResponse(
        "post", YURL(URL), status=HTTPStatus.FORBIDDEN
    )
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == SOURCE_REAUTH for f in flows)


@pytest.mark.freeze_time(NOW)
async def test_update_failure_marks_unavailable(
    hass: HomeAssistant, config_entry, mock_trias, trias_router, freezer
) -> None:
    """Failing polls make the entities unavailable, recovery restores them."""
    await setup_entry(hass, config_entry)
    entity_id = _entity_id(hass, config_entry, "departure_board", "departure_1")

    trias_router["stop_event"] = lambda body: AiohttpClientMockResponse(
        "post", YURL(URL), status=HTTPStatus.BAD_GATEWAY
    )
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    del trias_router["stop_event"]
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE


@pytest.mark.freeze_time(NOW)
async def test_unload(hass: HomeAssistant, config_entry, mock_trias) -> None:
    """Unload works."""
    await setup_entry(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.freeze_time(NOW)
async def test_remove_subentry_removes_device(
    hass: HomeAssistant, config_entry, mock_trias
) -> None:
    """Removing a subentry reloads and drops its device."""
    await setup_entry(hass, config_entry)
    board = next(
        s for s in config_entry.subentries.values() if s.subentry_type == "departure_board"
    )
    hass.config_entries.async_remove_subentry(config_entry, board.subentry_id)
    await hass.async_block_till_done()
    dev_reg = dr.async_get(hass)
    names = {d.name for d in dr.async_entries_for_config_entry(dev_reg, config_entry.entry_id)}
    assert names == {"Home → Work"}
    assert config_entry.state is ConfigEntryState.LOADED


@pytest.mark.freeze_time(NOW)
async def test_legacy_departure_sensors_migrated(
    hass: HomeAssistant, config_entry, mock_trias
) -> None:
    """next/following_departure become departure_1/2 and keep their entity ids."""
    config_entry.add_to_hass(hass)
    sub = next(s for s in config_entry.subentries.values() if s.subentry_type == "departure_board")
    ent_reg = er.async_get(hass)
    for key in ("next_departure", "following_departure"):
        ent_reg.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{sub.subentry_id}_{key}",
            config_entry=config_entry,
            config_subentry_id=sub.subentry_id,
            suggested_object_id=f"board_{key}",
        )

    await setup_entry(hass, config_entry)

    assert (
        ent_reg.async_get_entity_id("sensor", DOMAIN, f"{sub.subentry_id}_departure_1")
        == "sensor.board_next_departure"
    )
    assert (
        ent_reg.async_get_entity_id("sensor", DOMAIN, f"{sub.subentry_id}_departure_2")
        == "sensor.board_following_departure"
    )
    assert (
        ent_reg.async_get_entity_id("sensor", DOMAIN, f"{sub.subentry_id}_next_departure") is None
    )
    assert hass.states.get("sensor.board_next_departure").state == "2026-11-16T05:33:00+00:00"


@pytest.mark.freeze_time(NOW)
async def test_lower_count_removes_departure_sensors(
    hass: HomeAssistant, config_entry, mock_trias
) -> None:
    """Reducing the count drops the surplus numbered sensors."""
    await setup_entry(hass, config_entry)
    sub = next(s for s in config_entry.subentries.values() if s.subentry_type == "departure_board")
    ent_reg = er.async_get(hass)
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, f"{sub.subentry_id}_departure_3")

    hass.config_entries.async_update_subentry(config_entry, sub, data={**sub.data, "count": 1})
    await hass.async_block_till_done()

    assert ent_reg.async_get_entity_id("sensor", DOMAIN, f"{sub.subentry_id}_departure_1")
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, f"{sub.subentry_id}_departure_2") is None
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, f"{sub.subentry_id}_departure_3") is None


async def test_card_registered(hass: HomeAssistant) -> None:
    """The dashboard card is served and loaded in the frontend."""
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    hass.config.components.add("frontend")
    with patch("custom_components.uhrturm_oepnv.add_extra_js_url") as add_js:
        assert await async_setup_component(hass, DOMAIN, {})

    (paths,), _ = hass.http.async_register_static_paths.call_args
    assert paths[0].url_path == "/uhrturm_oepnv"
    assert (Path(paths[0].path) / "uhrturm-departures-card.js").is_file()
    add_js.assert_called_once()
    assert add_js.call_args.args[1].startswith("/uhrturm_oepnv/uhrturm-departures-card.js?v=")
