"""Number platform: writable numeric eBUS registers (opt-in via allow_write)."""
from __future__ import annotations

from typing import cast

from aioebus.codecs import value_range
from aioebus.engine import value_key
from aioebus.messages import FieldDef, MessageDef
from aioebus.translations import translation_key
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EbusConfigEntry
from .coordinator import EbusCoordinator
from .entities import icon_for, number_bounds
from .ids import control_uid

_UNIT_DEFAULT = (0.0, 100.0, 1.0)  # never used for the integer types numbers take


def _bounds(field: FieldDef) -> tuple[float, float, float]:
    """(min, max, step) an integer field can carry, from its type + divisor."""
    lo, hi, step = value_range(field.type, field.divisor) or _UNIT_DEFAULT
    return (lo, hi, round(step, 4))


async def async_setup_entry(
    hass: HomeAssistant, entry: EbusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        EbusNumber(coordinator, msg, field.field, field.label, cls)
        for msg, field, cls in coordinator.controls("number"))


class EbusNumber(CoordinatorEntity[EbusCoordinator], NumberEntity):
    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(
        self, coordinator: EbusCoordinator, msg: MessageDef, field: FieldDef, label: str,
        cls: str,
    ) -> None:
        super().__init__(coordinator)
        self._msg = msg
        self._key = value_key(msg, label)
        zz = int(msg.zz, 16)
        self._attr_unique_id = control_uid(coordinator.uid_prefix, msg, label)
        self._attr_translation_key = translation_key(msg, field)
        self._attr_icon = icon_for(field)
        if cls == "installer":  # a configuration setting: shown only once enabled
            self._attr_entity_category = EntityCategory.CONFIG
            self._attr_entity_registry_enabled_default = False
        bounds = number_bounds(coordinator.catalog.bounds(zz, msg.name), field)
        lo, hi, step = bounds or _bounds(field)
        self._attr_native_min_value = lo
        self._attr_native_max_value = hi
        self._attr_native_step = step
        if field.unit:
            self._attr_native_unit_of_measurement = field.unit
        self._attr_device_info = coordinator.device_info(zz)

    @property
    def available(self) -> bool:
        return super().available and not self.coordinator.bus_stale

    @property
    def native_value(self) -> float | None:
        return cast("float | None", self.coordinator.data.get(self._key))

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_write(self._msg, value)
