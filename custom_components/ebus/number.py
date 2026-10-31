"""Number platform: writable numeric eBUS registers (opt-in via allow_write)."""
from __future__ import annotations

from typing import cast

from aioebus.codecs import _NUMERIC, make_codec
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
from .library import FLOAT_TYPES, NUMBER_TYPES, fields_of, icon_for, number_bounds


def _bounds(field: FieldDef) -> tuple[float, float, float]:
    """Rough (min, max, step) for an integer field from its type + divisor."""
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
        kind = field.field.type.upper()
        if field.field.values or kind not in NUMBER_TYPES | FLOAT_TYPES:
            continue
        cls = coordinator.control_class(msg)
        if cls == "none":
            coordinator.remove_control("number", msg, field.label)
        else:
            entities.append(EbusNumber(coordinator, msg, field.field, field.label, cls))
    async_add_entities(entities)


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
        self._attr_unique_id = coordinator.uid(*self._key, "set")
        self._attr_translation_key = translation_key(msg, field)
        self._attr_icon = icon_for(field)
        if cls == "installer":  # a configuration setting: shown only once enabled
            self._attr_entity_category = EntityCategory.CONFIG
            self._attr_entity_registry_enabled_default = False
        bounds = number_bounds(coordinator.devices.get(zz), msg.name, field)
        lo, hi, step = bounds or _bounds(field)
        self._attr_native_min_value = lo
        self._attr_native_max_value = hi
        self._attr_native_step = step
        if field.unit:
            self._attr_native_unit_of_measurement = field.unit
        self._attr_device_info = coordinator.device_info(zz)

    @property
    def native_value(self) -> float | None:
        return cast("float | None", self.coordinator.data.get(self._key))

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_write(self._msg, value)
