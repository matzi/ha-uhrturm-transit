"""Diagnostics for TRIAS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_REQUESTOR_REF
from .coordinator import BoardCoordinator, CommuteCoordinator, TriasConfigEntry

TO_REDACT = {CONF_REQUESTOR_REF}
MAX_XML = 50_000


def _coordinator_diag(coordinator: BoardCoordinator | CommuteCoordinator) -> dict[str, Any]:
    diag: dict[str, Any] = {
        "type": coordinator.subentry.subentry_type,
        "title": coordinator.subentry.title,
        "data": dict(coordinator.subentry.data),
        "last_update_success": coordinator.last_update_success,
        "last_exception": repr(coordinator.last_exception) if coordinator.last_exception else None,
        "update_interval": str(coordinator.update_interval),
    }
    data = coordinator.data
    if isinstance(coordinator, BoardCoordinator) and data is not None:
        diag["result"] = {
            "fetched_at": data.fetched_at.isoformat(),
            "messages": data.messages,
            "departures": [d.as_dict() for d in data.departures],
        }
    elif isinstance(coordinator, CommuteCoordinator) and data is not None:
        diag["result"] = {
            "fetched_at": data.fetched_at.isoformat(),
            "active": data.active,
            "messages": data.messages,
            "options": [o.as_dict() for o in data.options],
        }
    return diag


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TriasConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    client = runtime.client
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "coordinators": {sid: _coordinator_diag(c) for sid, c in runtime.coordinators.items()},
        "last_request": client.last_request,
        "last_response": (client.last_response or "")[:MAX_XML],
        "last_warnings": client.last_warnings,
    }
