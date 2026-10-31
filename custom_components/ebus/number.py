"""Number platform: writable numeric eBUS registers (opt-in via allow_write)."""
from __future__ import annotations

from typing import cast

from aioebus.codecs import _NUMERIC, make_codec
from aioebus.engine import value_key
from aioebus.messages import FieldDef, MessageDef
from aioebus.translations import translation_key
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EbusConfigEntry
from .coordinator import EbusCoordinator
from .device import device_info
from .library import NUMBER_TYPES, fields_of, icon_for


def _bounds(field: FieldDef) -> tuple[float, float, float]:
    """Rough (min, max, step) for a numeric field from its type + divisor."""
    codec = make_codec(field.type)
    size = codec.length
    base_div = getattr(codec, "base_div", 1)
    extra = field.divisor if field.divisor and field.divisor > 0 else 1
    scale = base_div * extra
    signed = _NUMERIC.get(field.type.upper(), (0, False))[1]
    raw_max = (1 << (8 * size - (1 if signed else 0))) - 2
    lo = -(raw_max + 1) / scale if signed else 0.0
    return (lo, raw_max / scale, round(1 / scale, 4))


async def async_setup_entry(
    hass: HomeAssistant, entry: EbusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[EbusNumber] = []
    for msg in coordinator.write_messages:
        fields = fields_of(msg)
        if len(fields) != 1:
            continue  # only simple single-field writes for now
        field = fields[0]
        if field.field.type.upper() in NUMBER_TYPES and not field.field.values:
            entities.append(EbusNumber(coordinator, msg, field.field, field.label))
    async_add_entities(entities)


class EbusNumber(CoordinatorEntity[EbusCoordinator], NumberEntity):
    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(
        self, coordinator: EbusCoordinator, msg: MessageDef, field: FieldDef, label: str
    ) -> None:
        super().__init__(coordinator)
        self._msg = msg
        self._key = value_key(msg, label)
        zz = int(msg.zz, 16)
        self._attr_unique_id = f"ebus_{'_'.join(str(p) for p in self._key)}_set"
        self._attr_translation_key = translation_key(msg, field)
        self._attr_icon = icon_for(field)
        lo, hi, step = _bounds(field)
        self._attr_native_min_value = lo
        self._attr_native_max_value = hi
        self._attr_native_step = step
        if field.unit:
            self._attr_native_unit_of_measurement = field.unit
        self._attr_device_info = device_info(
            zz, coordinator.devices.get(zz), coordinator.host, coordinator.port)

    @property
    def native_value(self) -> float | None:
        return cast("float | None", self.coordinator.data.get(self._key))

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_write(self._msg, value)
