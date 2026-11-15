"""Base entities for TRIAS."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import CommuteOption, Departure
from .const import DOMAIN, STATE_TICK, SUBENTRY_BOARD
from .coordinator import BoardCoordinator, CommuteCoordinator


class TriasEntity[C: (BoardCoordinator, CommuteCoordinator)](CoordinatorEntity[C]):
    """Common base: one device per subentry, periodic state refresh."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: C, key: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        subentry = coordinator.subentry
        self._attr_translation_key = key
        self._attr_unique_id = f"{subentry.subentry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="TRIAS",
            model="Departure board" if subentry.subentry_type == SUBENTRY_BOARD else "Commute",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Re-evaluate state between polls so past departures disappear."""
        await super().async_added_to_hass()
        self.async_on_remove(async_track_time_interval(self.hass, self._async_tick, STATE_TICK))

    @callback
    def _async_tick(self, _now: datetime) -> None:
        self.async_write_ha_state()


class BoardEntity(TriasEntity[BoardCoordinator]):
    """Entity of a departure board."""

    def _reachable(self) -> list[Departure]:
        """All fetched departures that can still be reached."""
        if self.coordinator.data is None:
            return []
        limit = dt_util.now().replace(second=0, microsecond=0) + timedelta(
            minutes=self.coordinator.min_minutes
        )
        return [
            d
            for d in self.coordinator.data.departures
            if d.call.time is not None and d.call.time >= limit
        ]

    def upcoming(self) -> list[Departure]:
        """Departures that can still be reached, limited to the configured count."""
        return self._reachable()[: self.coordinator.count]

    def next_running(self, index: int = 0) -> Departure | None:
        """N-th reachable departure that is not cancelled (0-based).

        Not limited to the configured count, so a cancellation does not leave
        the last departure sensor empty while more departures were fetched.
        """
        running = [d for d in self._reachable() if not d.service.cancelled]
        return running[index] if len(running) > index else None


class CommuteEntity(TriasEntity[CommuteCoordinator]):
    """Entity of a commute."""

    def connections(self) -> list[CommuteOption]:
        """Connections you can still catch."""
        data = self.coordinator.data
        if data is None or not data.active:
            return []
        limit = dt_util.now().replace(second=0, microsecond=0)
        return [o for o in data.options if o.leave >= limit][: self.coordinator.count]

    def best(self) -> CommuteOption | None:
        """Best (next) connection that is not cancelled."""
        return next((o for o in self.connections() if not o.trip.cancelled), None)
