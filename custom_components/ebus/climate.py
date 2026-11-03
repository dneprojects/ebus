"""Climate platform: heating zones.

The aioebus catalog bundles each zone's registers (setpoint, room temperature,
operating mode) and names the mode values for off, automatic and heating: one
thermostat per zone, with HVAC mode / preset from the zone's operating mode
(off -> OFF, auto -> AUTO, others -> HEAT + preset)."""
from __future__ import annotations

from typing import Any

from aioebus.catalog import ClimateZone
from aioebus.messages import MessageDef
from aioebus.values import value_key, values_of
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EbusConfigEntry
from .coordinator import EbusCache, EbusCoordinator
from .ids import zone_uid


def _key(msg: MessageDef) -> tuple[str, str, str]:
    # zone registers are single-value: the cache key of that one value
    values = values_of(msg)
    return values[0].cache_key if values else value_key(msg, msg.name)


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
    pending = coordinator.catalog.zones()

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
    # defaults; a zone takes its setpoint register's range (from the aioebus catalog)
    _attr_min_temp = 5.0
    _attr_max_temp = 30.0
    _attr_target_temperature_step = 0.5

    def __init__(self, coordinator: EbusCoordinator, zone: ClimateZone) -> None:
        super().__init__(coordinator)
        self._zone = zone
        zz = int(zone.zz, 16)
        short = coordinator.data.get(_key(zone.shortname)) if zone.shortname else None
        if isinstance(short, str) and short.strip():
            self._attr_name = short  # the name the user gave the zone on the controller
        else:
            self._attr_translation_key = "zone"
            self._attr_translation_placeholders = {"n": str(zone.n)}
        self._attr_unique_id = zone_uid(coordinator.uid_prefix, zone.zz, zone.n)
        if zone.target_bounds is not None:  # the setpoint register's own range
            (self._attr_min_temp, self._attr_max_temp,
             self._attr_target_temperature_step) = zone.target_bounds
        self._attr_device_info = coordinator.device_info(zz)

        labels = _enum_labels(zone.mode_read) or _enum_labels(zone.mode_write)
        self._off = zone.modes.get("off")
        self._auto = zone.modes.get("auto")
        self._presets = [v for v in labels if v not in (self._off, self._auto)]
        self._heat_default = zone.modes.get("heat") or (self._presets[0] if self._presets else None)

        hvac: list[HVACMode] = [HVACMode.HEAT]
        if self._off:
            hvac.insert(0, HVACMode.OFF)
        if self._auto:
            hvac.append(HVACMode.AUTO)
        self._attr_hvac_modes = hvac

        feature = ClimateEntityFeature(0)
        self._read_only = not coordinator.allow_write
        if self._read_only:  # show the zone, but offer no controls that must fail
            self._attr_supported_features = feature
            return
        if zone.target_write is not None:
            feature |= ClimateEntityFeature.TARGET_TEMPERATURE
        if zone.mode_write is not None:
            if self._off:
                feature |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
            if self._presets:
                self._attr_preset_modes = self._presets
                feature |= ClimateEntityFeature.PRESET_MODE
        self._attr_supported_features = feature

    @property
    def available(self) -> bool:
        return super().available and not self.coordinator.bus_stale

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
    def hvac_modes(self) -> list[HVACMode]:
        # read-only: only the current mode, so the UI offers nothing to switch to
        return [self.hvac_mode] if self._read_only else self._attr_hvac_modes

    @property
    def hvac_mode(self) -> HVACMode:
        value = self._val(self._zone.mode_read)
        if value is not None and value == self._off:
            return HVACMode.OFF
        if value is not None and value == self._auto:
            return HVACMode.AUTO
        return HVACMode.HEAT

    @property
    def preset_mode(self) -> str | None:
        value = self._val(self._zone.mode_read)
        return value if isinstance(value, str) and value in self._presets else None

    async def async_set_temperature(self, **kwargs: Any) -> None:
        mode = kwargs.get(ATTR_HVAC_MODE)  # set_temperature may switch too
        if mode is not None and mode != self.hvac_mode:
            await self.async_set_hvac_mode(mode)
        temp = kwargs.get(ATTR_TEMPERATURE)
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
        await self.coordinator.async_write(msg, value)
