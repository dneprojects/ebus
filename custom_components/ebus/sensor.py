"""Sensor platform: one entity per decodable read field of every scanned device."""
from __future__ import annotations

from typing import Final, cast

from aioebus.engine import value_key
from aioebus.translations import translation_key
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EbusConfigEntry
from .coordinator import EbusCoordinator
from .device import device_info
from .library import Field, fields_of, icon_for

_UNIT_CLASS: Final[dict[str, tuple[SensorDeviceClass | None, SensorStateClass | None]]] = {
    "°C": (SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
    "bar": (SensorDeviceClass.PRESSURE, SensorStateClass.MEASUREMENT),
    "%": (None, SensorStateClass.MEASUREMENT),
    "kW": (SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
    "W": (SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
    "kWh": (SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: EbusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities = [
        EbusSensor(coordinator, field)
        for msg in coordinator.read_messages
        for field in fields_of(msg)
    ]
    async_add_entities(entities)


class EbusSensor(CoordinatorEntity[EbusCoordinator], SensorEntity):
    """A single decoded eBUS register value."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EbusCoordinator, field: Field) -> None:
        super().__init__(coordinator)
        self._field = field
        self._key = value_key(field.msg, field.label)
        zz = int(field.msg.zz, 16)
        self._attr_unique_id = f"ebus_{'_'.join(str(p) for p in self._key)}"
        self._attr_translation_key = translation_key(field.msg, field.field)
        self._attr_icon = icon_for(field.field)
        # the bundled defs are already the curated set of registers that carry a
        # value on this installation, so read entities are enabled by default
        self._attr_entity_registry_enabled_default = True

        if field.field.values:  # value table -> enum sensor with its labels
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(dict.fromkeys(field.field.values.values()))
        elif field.field.unit:
            self._attr_native_unit_of_measurement = field.field.unit
            dev_cls, state_cls = _UNIT_CLASS.get(field.field.unit, (None, None))
            self._attr_device_class = dev_cls
            self._attr_state_class = state_cls

        self._attr_device_info = device_info(zz, coordinator.devices.get(zz))

    @property
    def native_value(self) -> StateType:
        value = self.coordinator.data.get(self._key)
        # an ENUM sensor must only report one of its declared options
        options = self._attr_options
        if options is not None and value not in options:
            return None
        return cast(StateType, value)

    @property
    def available(self) -> bool:
        return super().available and self._key in self.coordinator.data
