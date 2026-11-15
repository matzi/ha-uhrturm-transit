"""Sensors for TRIAS departure boards and commutes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import Platform, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_STOP_NAME, SUBENTRY_BOARD, SUBENTRY_COMMUTE
from .coordinator import BoardCoordinator, CommuteCoordinator, TriasConfigEntry
from .entity import BoardEntity, CommuteEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TriasConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors, grouped by subentry."""
    ent_reg = er.async_get(hass)
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        entities: list[SensorEntity] = []
        if coordinator.subentry.subentry_type == SUBENTRY_BOARD:
            assert isinstance(coordinator, BoardCoordinator)
            entities = [DepartureSensor(coordinator, index) for index in range(coordinator.count)]
            _remove_surplus_departures(ent_reg, entry, subentry_id, coordinator.count)
        elif coordinator.subentry.subentry_type == SUBENTRY_COMMUTE:
            assert isinstance(coordinator, CommuteCoordinator)
            entities = [
                LeaveAtSensor(coordinator, "leave_at"),
                StationSensor(coordinator, "station"),
                CommuteDepartureSensor(coordinator, "departure"),
                ArrivalSensor(coordinator, "arrival"),
                CommuteDelaySensor(coordinator, "delay"),
            ]
        async_add_entities(entities, config_subentry_id=subentry_id)


# --------------------------------------------------------------------------- board


def _departure_key(index: int) -> str:
    """Unique-id suffix of the departure sensor at 0-based `index`."""
    return f"departure_{index + 1}"


@callback
def _remove_surplus_departures(
    ent_reg: er.EntityRegistry, entry: TriasConfigEntry, subentry_id: str, count: int
) -> None:
    """Drop departure sensors left over from a higher configured count."""
    prefix = f"{subentry_id}_departure_"
    for reg_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if reg_entry.domain != Platform.SENSOR or not reg_entry.unique_id.startswith(prefix):
            continue
        suffix = reg_entry.unique_id.removeprefix(prefix)
        if suffix.isdigit() and int(suffix) > count:
            ent_reg.async_remove(reg_entry.entity_id)


class DepartureSensor(BoardEntity, SensorEntity):
    """N-th upcoming (not cancelled) departure as timestamp."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({"departures"})

    def __init__(self, coordinator: BoardCoordinator, index: int) -> None:
        """Initialize."""
        super().__init__(coordinator, _departure_key(index))
        self._attr_translation_key = "board_departure"
        self._attr_translation_placeholders = {"index": str(index + 1)}
        self._index = index

    @property
    def native_value(self) -> datetime | None:
        """Expected departure time."""
        dep = self.next_running(self._index)
        return dep.call.time if dep else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Details; the first sensor also carries the full list."""
        dep = self.next_running(self._index)
        attrs: dict[str, Any] = {
            "index": self._index + 1,
            "stop": self.coordinator.subentry.data.get(CONF_STOP_NAME),
            "line": dep.service.line if dep else None,
            "destination": dep.service.destination if dep else None,
            "planned": dep.call.timetabled.isoformat() if dep and dep.call.timetabled else None,
            "delay": dep.call.delay_min if dep else None,
            "realtime": dep.call.estimated is not None if dep else None,
            "platform": dep.call.bay if dep else None,
        }
        if self._index == 0:
            attrs["departures"] = [d.as_dict() for d in self.upcoming()]
        return attrs


# --------------------------------------------------------------------------- commute


class LeaveAtSensor(CommuteEntity, SensorEntity):
    """When to leave home."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({"connections"})

    @property
    def native_value(self) -> datetime | None:
        """Leave-home time of the best connection."""
        best = self.best()
        return best.leave if best else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Details of the best connection plus alternatives."""
        best = self.best()
        data = self.coordinator.data
        return {
            "active": bool(data and data.active),
            "station": best.station if best else None,
            "board_stop": best.board_stop if best else None,
            "lines": [leg.service.line for leg in best.trip.pt_legs if leg.service]
            if best
            else None,
            "departure": best.departure.isoformat() if best else None,
            "arrival": best.arrive.isoformat() if best else None,
            "changes": best.trip.interchanges if best else None,
            "delay": best.delay_min if best else None,
            "connections": [o.as_dict() for o in self.connections()],
        }


class StationSensor(CommuteEntity, SensorEntity):
    """Which home station to walk to."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator: CommuteCoordinator, key: str) -> None:
        """Initialize."""
        super().__init__(coordinator, key)
        self._attr_options = list(dict.fromkeys(s.name for s in coordinator.stations))

    @property
    def native_value(self) -> str | None:
        """Station name."""
        best = self.best()
        return best.station if best else None


class CommuteDepartureSensor(CommuteEntity, SensorEntity):
    """Departure of the first vehicle of the best connection."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        """Departure time."""
        best = self.best()
        return best.departure if best else None


class ArrivalSensor(CommuteEntity, SensorEntity):
    """Arrival at work of the best connection."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        """Arrival time incl. walk from the work stop."""
        best = self.best()
        return best.arrive if best else None


class CommuteDelaySensor(CommuteEntity, SensorEntity):
    """Delay of the first vehicle of the best connection."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    @property
    def native_value(self) -> int | None:
        """Delay in minutes (None without real-time data)."""
        best = self.best()
        return best.delay_min if best else None
