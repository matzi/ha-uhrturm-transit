"""The TRIAS public transport integration."""

from __future__ import annotations

import asyncio

from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import TriasClient
from .const import CONF_REQUESTOR_REF, DOMAIN, SUBENTRY_BOARD, SUBENTRY_COMMUTE
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
    """Set up the integration (actions)."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TriasConfigEntry) -> bool:
    """Set up a TRIAS endpoint with its departure boards and commutes."""
    client = TriasClient(
        async_get_clientsession(hass),
        entry.data[CONF_URL],
        entry.data.get(CONF_REQUESTOR_REF, ""),
    )
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


async def async_unload_entry(hass: HomeAssistant, entry: TriasConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: TriasConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
