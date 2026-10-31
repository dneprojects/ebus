"""Binary sensor platform: the adapter's own connection state.

A single connectivity entity on the adapter device, driven by the engine's live
link state (set true while the stream is up and bytes arrive, false when it drops
or stops). It mirrors the bridge's "Signal" sensor."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EbusConfigEntry
from .coordinator import EbusCoordinator
from .device import adapter_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: EbusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([EbusAdapterSignal(coordinator)])


class EbusAdapterSignal(CoordinatorEntity[EbusCoordinator], BinarySensorEntity):
    """Is the adapter link up and the bus alive?"""

    _attr_has_entity_name = True
    _attr_name = "Signal"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: EbusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.uid("adapter", "signal")
        self._attr_device_info = adapter_device_info()

    @property
    def is_on(self) -> bool:
        engine = self.coordinator.engine
        return engine is not None and engine.stats.connected
