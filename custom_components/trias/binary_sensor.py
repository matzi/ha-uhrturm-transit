"""Disruption binary sensors for TRIAS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_DELAY_THRESHOLD, DEFAULT_DELAY_THRESHOLD, SUBENTRY_BOARD
from .coordinator import BoardCoordinator, CommuteCoordinator, TriasConfigEntry
from .entity import BoardEntity, CommuteEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TriasConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one disruption sensor per subentry."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        entity: BinarySensorEntity
        if coordinator.subentry.subentry_type == SUBENTRY_BOARD:
            assert isinstance(coordinator, BoardCoordinator)
            entity = BoardDisruptionSensor(coordinator, "disruption")
        else:
            assert isinstance(coordinator, CommuteCoordinator)
            entity = CommuteDisruptionSensor(coordinator, "disruption")
        async_add_entities([entity], config_subentry_id=subentry_id)


def _threshold(coordinator: BoardCoordinator | CommuteCoordinator) -> int:
    return int(coordinator.subentry.data.get(CONF_DELAY_THRESHOLD, DEFAULT_DELAY_THRESHOLD))


class BoardDisruptionSensor(BoardEntity, BinarySensorEntity):
    """On if a shown departure is cancelled or delayed beyond the threshold."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def _affected(self) -> list[dict[str, Any]]:
        threshold = _threshold(self.coordinator)
        return [
            d.as_dict()
            for d in self.upcoming()
            if d.service.cancelled or (d.call.delay_min or 0) >= threshold
        ]

    @property
    def is_on(self) -> bool:
        """Problem detected."""
        return bool(self._affected())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Affected departures."""
        return {"threshold": _threshold(self.coordinator), "affected": self._affected()}


class CommuteDisruptionSensor(CommuteEntity, BinarySensorEntity):
    """On if the next connection is cancelled or delayed beyond the threshold."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool | None:
        """Problem with the next catchable connection."""
        options = self.connections()
        if not options:
            return None if not (self.coordinator.data and self.coordinator.data.active) else False
        first = options[0]
        return first.trip.cancelled or first.max_delay_min >= _threshold(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Details."""
        options = self.connections()
        first = options[0] if options else None
        return {
            "threshold": _threshold(self.coordinator),
            "cancelled": first.trip.cancelled if first else None,
            "max_delay": first.max_delay_min if first else None,
            "lines": [leg.service.line for leg in first.trip.pt_legs if leg.service]
            if first
            else None,
        }
