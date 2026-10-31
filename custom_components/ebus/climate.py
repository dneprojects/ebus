"""Climate platform: heating zones.

Zones are bundled from per-zone registers declared in index.json (not hardcoded
here): one thermostat per zone, with target = the writable comfort setpoint,
current = the room temperature, and HVAC mode / preset from the zone's operating
mode (off -> OFF, auto -> AUTO, others -> HEAT + preset)."""
from __future__ import annotations

from typing import Any

from aioebus.active import ReadError
from aioebus.engine import value_key
from aioebus.messages import MessageDef
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EbusConfigEntry
from .coordinator import EbusCache, EbusCoordinator
from .device import device_info
from .library import ClimateZone, climate_zones


def _key(msg: MessageDef) -> tuple[str, str, str]:
    # zone registers are single-value, so the cache field is the message name
    return value_key(msg, msg.name)


def _enum_labels(msg: MessageDef | None) -> list[str]:
    if msg is not None:
        for field in msg.fields:
            if field.values:
                return list(dict.fromkeys(field.values.values()))
    return []


async def async_setup_entry(
    hass: HomeAssistant, entry: EbusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    pending = climate_zones(coordinator.devices, coordinator.user_dir)

    # a zone is in use once one of its registers has a value; on a fresh install
    # nothing is cached yet, so keep checking as data arrives
    @callback
    def _add_active_zones() -> None:
        active = [z for z in pending if any(
            coordinator.data.get(_key(m)) is not None
            for m in (z.target_read, z.current, z.mode_read) if m is not None)]
        if active:
            for zone in active:
                pending.remove(zone)
            async_add_entities(EbusZoneClimate(coordinator, z) for z in active)

    _add_active_zones()
    if pending:
        entry.async_on_unload(coordinator.async_add_listener(_add_active_zones))


class EbusZoneClimate(CoordinatorEntity[EbusCoordinator], ClimateEntity):
    """One heating zone as a thermostat."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 5
    _attr_max_temp = 30
    _attr_target_temperature_step = 0.5

    def __init__(self, coordinator: EbusCoordinator, zone: ClimateZone) -> None:
        super().__init__(coordinator)
        self._zone = zone
        zz = int(zone.zz, 16)
        short = coordinator.data.get(_key(zone.shortname)) if zone.shortname else None
        self._attr_name = short if isinstance(short, str) and short.strip() else zone.name
        self._attr_unique_id = f"ebus_{zone.zz}_z{zone.n}_climate"
        self._attr_device_info = device_info(
            zz, coordinator.devices.get(zz), coordinator.host, coordinator.port)

        labels = _enum_labels(zone.mode_read) or _enum_labels(zone.mode_write)
        low = {v.lower(): v for v in labels}
        self._off = low.get("off")
        self._auto = low.get("auto")
        self._presets = [v for v in labels if v.lower() not in ("off", "auto")]
        self._heat_default = low.get("day") or (self._presets[0] if self._presets else None)

        hvac: list[HVACMode] = [HVACMode.HEAT]
        if self._off:
            hvac.insert(0, HVACMode.OFF)
        if self._auto:
            hvac.append(HVACMode.AUTO)
        self._attr_hvac_modes = hvac

        feature = ClimateEntityFeature(0)
        if zone.target_write is not None:
            feature |= ClimateEntityFeature.TARGET_TEMPERATURE
        if zone.mode_write is not None:
            if self._off:
                feature |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
            if self._presets:
                self._attr_preset_modes = self._presets
                feature |= ClimateEntityFeature.PRESET_MODE
        self._attr_supported_features = feature

    def _data(self) -> EbusCache:
        return self.coordinator.data

    def _val(self, msg: MessageDef | None) -> object:
        return self._data().get(_key(msg)) if msg is not None else None

    @property
    def current_temperature(self) -> float | None:
        try:
            return float(self._val(self._zone.current))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    @property
    def target_temperature(self) -> float | None:
        try:
            return float(self._val(self._zone.target_read))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    @property
    def hvac_mode(self) -> HVACMode:
        value = str(self._val(self._zone.mode_read) or "").lower()
        if value == "off":
            return HVACMode.OFF
        if value == "auto":
            return HVACMode.AUTO
        return HVACMode.HEAT

    @property
    def preset_mode(self) -> str | None:
        value = self._val(self._zone.mode_read)
        return value if isinstance(value, str) and value in self._presets else None

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get("temperature")
        if temp is not None and self._zone.target_write is not None:
            await self._write(self._zone.target_write, temp)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        label = {
            HVACMode.OFF: self._off,
            HVACMode.AUTO: self._auto,
            HVACMode.HEAT: self._heat_default,
        }.get(hvac_mode)
        if label and self._zone.mode_write is not None:
            await self._write(self._zone.mode_write, label)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode in self._presets and self._zone.mode_write is not None:
            await self._write(self._zone.mode_write, preset_mode)

    async def async_turn_off(self) -> None:
        if self._off and self._zone.mode_write is not None:
            await self._write(self._zone.mode_write, self._off)

    async def async_turn_on(self) -> None:
        if self._heat_default and self._zone.mode_write is not None:
            await self._write(self._zone.mode_write, self._heat_default)

    async def _write(self, msg: MessageDef, value: object) -> None:
        assert self.coordinator.engine is not None
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        try:
            await self.coordinator.engine.write(msg, {msg.name: value})
        except (TimeoutError, ReadError, ValueError, OSError) as err:
            raise HomeAssistantError(f"eBUS write to {msg.name} failed: {err}") from err
        await self.coordinator.async_request_refresh()
