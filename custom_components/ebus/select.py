"""Select platform: writable enum eBUS registers (opt-in via allow_write)."""
from __future__ import annotations

from typing import cast

from aioebus.engine import value_key
from aioebus.messages import FieldDef, MessageDef
from aioebus.translations import translation_key
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EbusConfigEntry
from .coordinator import EbusCoordinator
from .library import fields_of, icon_for


async def async_setup_entry(
    hass: HomeAssistant, entry: EbusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[EbusSelect] = []
    for msg in coordinator.write_messages:
        fields = fields_of(msg)
        if len(fields) != 1:
            continue
        field = fields[0]
        if field.field.values:  # value table -> selectable options
            entities.append(EbusSelect(coordinator, msg, field.field, field.label))
    async_add_entities(entities)


class EbusSelect(CoordinatorEntity[EbusCoordinator], SelectEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: EbusCoordinator, msg: MessageDef, field: FieldDef, label: str
    ) -> None:
        super().__init__(coordinator)
        assert field.values is not None  # only built for value-table fields
        self._msg = msg
        self._key = value_key(msg, label)
        zz = int(msg.zz, 16)
        self._attr_unique_id = coordinator.uid(*self._key, "set")
        self._attr_translation_key = translation_key(msg, field)
        self._attr_icon = icon_for(field)
        self._attr_options = list(dict.fromkeys(field.values.values()))
        self._attr_device_info = coordinator.device_info(zz)

    @property
    def current_option(self) -> str | None:
        value = self.coordinator.data.get(self._key)
        return cast("str | None", value) if value in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_write(self._msg, option)
