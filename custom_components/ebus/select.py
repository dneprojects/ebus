"""Select platform: writable enum eBUS registers (opt-in via allow_write)."""
from __future__ import annotations

from typing import cast

from aioebus.values import Value
from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EbusConfigEntry
from .coordinator import EbusCoordinator
from .entities import icon_for
from .ids import control_uid


async def async_setup_entry(
    hass: HomeAssistant, entry: EbusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        EbusSelect(coordinator, value, cls) for value, cls in coordinator.controls("select"))


class EbusSelect(CoordinatorEntity[EbusCoordinator], SelectEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: EbusCoordinator, value: Value, cls: str) -> None:
        super().__init__(coordinator)
        msg, field = value.msg, value.field
        assert field.values is not None  # only built for value-table fields
        self._msg = msg
        self._key = value.cache_key
        zz = coordinator.catalog.owner(msg)
        self._attr_unique_id = control_uid(coordinator.uid_prefix, msg, value.label)
        self._attr_translation_key = value.key
        self._attr_icon = icon_for(field)
        if cls == "installer":  # a configuration setting: shown only once enabled
            self._attr_entity_category = EntityCategory.CONFIG
            self._attr_entity_registry_enabled_default = False
        self._attr_options = list(dict.fromkeys(field.values.values()))
        self._attr_device_info = coordinator.device_info(zz)

    @property
    def available(self) -> bool:
        return super().available and not self.coordinator.bus_stale

    @property
    def current_option(self) -> str | None:
        value = self.coordinator.data.get(self._key)
        return cast("str | None", value) if value in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_write(self._msg, option)
