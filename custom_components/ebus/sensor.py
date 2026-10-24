"""Sensor platform: one entity per decodable read field of every scanned device."""
from __future__ import annotations

from collections.abc import Callable
from typing import Final, cast

from aioebus.engine import value_key
from aioebus.translations import translation_key
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EbusConfigEntry
from .coordinator import EbusCoordinator
from .device import adapter_device_info, device_info
from .library import Field, disabled_field_labels, fields_of, icon_for

_UNIT_CLASS: Final[dict[str, tuple[SensorDeviceClass | None, SensorStateClass | None]]] = {
    "°C": (SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
    "bar": (SensorDeviceClass.PRESSURE, SensorStateClass.MEASUREMENT),
    "%": (None, SensorStateClass.MEASUREMENT),
    "kW": (SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
    "W": (SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
    "kWh": (SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
}

# eBUS field types that decode to a number (so the sensor can carry long-term
# statistics); text, date/time, PIN, hex and bit-flag fields are excluded
_NUMERIC_TYPES: Final[frozenset[str]] = frozenset({
    "UCH", "SCH", "UIN", "SIN", "ULG", "U2L", "U4L", "UIR", "SIR",
    "D1B", "D1C", "D2B", "D2C", "FLT", "EXP", "EXR", "BCD"})


# the adapter's own diagnostic sensors (name, value, unit, state class, icon),
# attached to the adapter device alongside the connection binary sensor
_AdapterSpec = tuple[str, Callable[[EbusCoordinator], StateType], str | None,
                     SensorStateClass | None, str]
_ADAPTER_SENSORS: Final[list[_AdapterSpec]] = [
    ("Host", lambda c: f"{c.host}:{c.port}", None, None, "mdi:ip-network"),
    ("Telegramme", lambda c: c.engine.stats.telegrams if c.engine else None,
     None, SensorStateClass.TOTAL_INCREASING, "mdi:bus"),
    ("Abfragen beantwortet", lambda c: c.engine.stats.polls_ok if c.engine else None,
     None, SensorStateClass.TOTAL_INCREASING, "mdi:check-network"),
    ("Abfragen ohne Antwort", lambda c: c.engine.stats.polls_failed if c.engine else None,
     None, SensorStateClass.TOTAL_INCREASING, "mdi:network-off"),
    ("Werte zwischengespeichert", lambda c: len(c.engine.cache) if c.engine else None,
     None, SensorStateClass.MEASUREMENT, "mdi:database"),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: EbusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        EbusSensor(coordinator, field)
        for msg in coordinator.read_messages
        for field in fields_of(msg)
    ]
    entities += [EbusAdapterSensor(coordinator, *spec) for spec in _ADAPTER_SENSORS]
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
        # channels known to be unconnected on this device type (from the def
        # metadata, so the decision is static at registration) are off by default
        scan = coordinator.devices.get(zz)
        dead = disabled_field_labels(scan) if scan is not None else set()
        self._attr_entity_registry_enabled_default = field.label not in dead

        if field.field.values:  # value table -> enum sensor with its labels
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(dict.fromkeys(field.field.values.values()))
        else:
            unit = field.field.unit
            if unit:
                self._attr_native_unit_of_measurement = unit
            dev_cls, unit_state = _UNIT_CLASS.get(unit, (None, None))
            self._attr_device_class = dev_cls
            # a numeric reading gets long-term statistics: energy accumulates
            # (total_increasing), everything else is a measurement; text, date/
            # time and flag fields stay without a state class
            if field.field.type.upper().split(":", 1)[0] in _NUMERIC_TYPES:
                self._attr_state_class = unit_state or SensorStateClass.MEASUREMENT

        self._attr_device_info = device_info(
            zz, coordinator.devices.get(zz), coordinator.host, coordinator.port)

    @property
    def native_value(self) -> StateType:
        value = self.coordinator.data.get(self._key)
        # an ENUM sensor must only report one of its declared options; _attr_options
        # is set only for those and raises when unset (HA cached-property backing),
        # so read it defensively -- a non-enum sensor has no options to enforce
        options = getattr(self, "_attr_options", None)
        if options is not None and value not in options:
            return None
        return cast(StateType, value)

    @property
    def available(self) -> bool:
        return super().available and self._key in self.coordinator.data


class EbusAdapterSensor(CoordinatorEntity[EbusCoordinator], SensorEntity):
    """A diagnostic reading about the adapter link itself (not a bus register)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: EbusCoordinator,
        name: str,
        value: Callable[[EbusCoordinator], StateType],
        unit: str | None,
        state_class: SensorStateClass | None,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._value = value
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class
        self._attr_icon = icon
        slug = name.lower().replace(" ", "_")
        self._attr_unique_id = f"ebus_{coordinator.host}_{coordinator.port}_{slug}"
        self._attr_device_info = adapter_device_info(coordinator.host, coordinator.port)

    @property
    def native_value(self) -> StateType:
        return self._value(self.coordinator)
