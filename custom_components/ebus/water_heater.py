"""Water heater platform: a controller's hot water.

The aioebus catalog bundles the registers (setpoint, cylinder temperature,
operating mode, special function) and names the mode values for off, automatic
(time program) and manual. A one-time charge is the special function: it starts
with that operation and ends when another one is chosen, or when the controller
ends it. Whether the controller then charges at all is its decision: it does not
while the cylinder is within its charge hysteresis of the setpoint."""
from __future__ import annotations

from typing import Any, Final

from aioebus.catalog import HotWater
from aioebus.messages import MessageDef
from homeassistant.components.water_heater import (  # type: ignore[attr-defined, unused-ignore]
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EbusConfigEntry
from .coordinator import EbusCache, EbusCoordinator
from .entity import EbusSettlingEntity
from .ids import hot_water_uid
from .zone_state import value_of

_BOOST: Final = "boost"

PARALLEL_UPDATES = 0  # the coordinator updates; the engine queues writes


def _in_use(data: EbusCache, water: HotWater) -> bool:
    return any(value_of(data, m) is not None
               for m in (water.mode_read, water.target_read, water.current))


async def async_setup_entry(
    hass: HomeAssistant, entry: EbusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    pending = coordinator.catalog.hot_water()

    # once one of its registers has a value; nothing is cached on a fresh install
    @callback
    def _add_active() -> None:
        active = [w for w in pending if _in_use(coordinator.data, w)]
        if active:
            for water in active:
                pending.remove(water)
            async_add_entities(EbusHotWater(coordinator, w) for w in active)

    _add_active()
    if pending:
        entry.async_on_unload(coordinator.async_add_listener(_add_active))


class EbusHotWater(EbusSettlingEntity, WaterHeaterEntity):
    """A controller's hot water."""

    _attr_translation_key = "hot_water"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_TENTHS
    # defaults; the setpoint register's range when the catalog names one
    _attr_min_temp = 30.0
    _attr_max_temp = 70.0

    def __init__(self, coordinator: EbusCoordinator, water: HotWater) -> None:
        super().__init__(coordinator)
        self._water = water
        self._attr_unique_id = hot_water_uid(coordinator.uid_prefix, water.zz)
        self._attr_device_info = coordinator.device_info(int(water.zz, 16))
        if water.target_bounds is not None:
            self._attr_min_temp, self._attr_max_temp, _ = water.target_bounds
        self._boost = (water.special_values.get(_BOOST)
                       if water.special_write is not None else None)
        self._attr_operation_list = [
            *water.modes, *([_BOOST] if self._boost else [])]
        feature = WaterHeaterEntityFeature(0)
        if coordinator.allow_write:
            if water.target_write is not None:
                feature |= WaterHeaterEntityFeature.TARGET_TEMPERATURE
            if water.mode_write is not None:
                feature |= WaterHeaterEntityFeature.OPERATION_MODE
        self._attr_supported_features = feature

    def _val(self, msg: MessageDef | None) -> object:
        return value_of(self.coordinator.data, msg)

    def _float(self, msg: MessageDef | None) -> float | None:
        try:
            return float(self._val(msg))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    @property
    def current_temperature(self) -> float | None:
        return self._float(self._water.current)

    @property
    def target_temperature(self) -> float | None:
        return self._float(self._water.target_read)

    def _charging(self) -> bool:
        value = self._val(self._water.special_read)
        return value is not None and value == self._boost

    @property
    def current_operation(self) -> str | None:
        if self._charging():
            return _BOOST
        value = self._val(self._water.mode_read)
        return next((role for role, label in self._water.modes.items() if label == value), None)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        water = self._water
        if temp is not None and water.target_write is not None:
            await self.coordinator.async_write(water.target_write, temp)
            await self._read_change((water.target_read,), ())

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        water = self._water
        label = water.modes.get(operation_mode)
        if operation_mode != _BOOST and (label is None or water.mode_write is None):
            return
        if operation_mode == _BOOST and not self._boost:
            return
        if operation_mode == _BOOST:
            if not self._charging() and water.special_write is not None:
                await self.coordinator.async_write(water.special_write, self._boost)
        else:
            if self._charging() and water.special_write is not None:
                await self.coordinator.async_write(
                    water.special_write, water.special_values["none"])
            if water.mode_write is not None and self._val(water.mode_read) != label:
                await self.coordinator.async_write(water.mode_write, label)
        await self._read_change((water.mode_read, water.special_read),
                                (water.special_read, water.target_read))
