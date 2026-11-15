"""The TRIAS public transport integration."""

from __future__ import annotations

import asyncio
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .api import TriasClient
from .const import (
    CARD_FILENAME,
    CARD_URL_PATH,
    CONF_REQUESTOR_REF,
    DOMAIN,
    SUBENTRY_BOARD,
    SUBENTRY_COMMUTE,
)
from .coordinator import (
    BoardCoordinator,
    CommuteCoordinator,
    TriasConfigEntry,
    TriasRuntimeData,
)
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration (actions, dashboard card)."""
    async_setup_services(hass)
    await _async_register_card(hass)
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the departure card and load it in the frontend (no manual resource needed)."""
    if hass.http is None or "frontend" not in hass.config.components:
        return  # e.g. tests or a setup without the web UI
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_PATH, str(Path(__file__).parent / "frontend"), False)]
    )
    version = (await async_get_integration(hass, DOMAIN)).version
    add_extra_js_url(hass, f"{CARD_URL_PATH}/{CARD_FILENAME}?v={version}")


async def async_setup_entry(hass: HomeAssistant, entry: TriasConfigEntry) -> bool:
    """Set up a TRIAS endpoint with its departure boards and commutes."""
    client = TriasClient(
        async_get_clientsession(hass),
        entry.data[CONF_URL],
        entry.data.get(CONF_REQUESTOR_REF, ""),
    )
    _migrate_departure_unique_ids(hass, entry)
    runtime = TriasRuntimeData(client=client)
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_BOARD:
            runtime.coordinators[subentry.subentry_id] = BoardCoordinator(
                hass, entry, subentry, client
            )
        elif subentry.subentry_type == SUBENTRY_COMMUTE:
            runtime.coordinators[subentry.subentry_id] = CommuteCoordinator(
                hass, entry, subentry, client
            )

    await asyncio.gather(
        *(c.async_config_entry_first_refresh() for c in runtime.coordinators.values())
    )
    entry.runtime_data = runtime

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # adding/removing/editing subentries triggers a reload
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


# old fixed board sensors -> numbered departure sensors (entity ids are kept)
_LEGACY_DEPARTURE_KEYS = {"next_departure": "departure_1", "following_departure": "departure_2"}


@callback
def _migrate_departure_unique_ids(hass: HomeAssistant, entry: TriasConfigEntry) -> None:
    ent_reg = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if reg_entry.domain != Platform.SENSOR:
            continue
        for old, new in _LEGACY_DEPARTURE_KEYS.items():
            if not reg_entry.unique_id.endswith(f"_{old}"):
                continue
            new_unique_id = reg_entry.unique_id.removesuffix(old) + new
            if ent_reg.async_get_entity_id(Platform.SENSOR, DOMAIN, new_unique_id):
                ent_reg.async_remove(reg_entry.entity_id)  # target exists already
            else:
                ent_reg.async_update_entity(reg_entry.entity_id, new_unique_id=new_unique_id)
            break


async def async_unload_entry(hass: HomeAssistant, entry: TriasConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: TriasConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
