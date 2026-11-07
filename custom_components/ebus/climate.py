"""Climate platform: heating zones.

The aioebus catalog bundles each zone's registers (setpoints, room temperature,
operating mode, quick veto) and names the mode values for off, automatic and
manual heating: one thermostat per zone, with the HVAC mode from the zone's
operating mode (off -> OFF, auto -> AUTO, others -> HEAT; any further value is
a preset). It shows the setpoint in effect now (none while off); a new one is
the manual setpoint while heating manually, and a quick veto (a temporary
override, as the controller's own app does) in automatic mode. Presets set
and end an away period (from today; ended as a period that has passed) and
airing; the preset shows what the zone's state (zone_state.py) tells. After a
change the setpoint is read at once and again a few seconds later, once the
controller has recalculated it."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Final

from aioebus.catalog import ClimateZone
from aioebus.messages import MessageDef
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    PRESET_AWAY,
    PRESET_NONE,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import EbusConfigEntry
from .coordinator import EbusCache, EbusCoordinator
from .entities import options_of
from .entity import EbusSettlingEntity
from .ids import zone_uid
from .zone_state import AIRING, AWAY, in_use, value_of, zone_state

# hours of a quick veto when the controller has not reported its own duration
_VETO_HOURS: Final = 3.0
# the airing preset (special function "ventilation"), and the length of an away
# period without an end, as the myVaillant library sets it
PRESET_AIRING: Final = "ventilation"
_AWAY_DAYS: Final = 365


def _enum_labels(msg: MessageDef | None) -> list[str]:
    if msg is not None:
        for field in msg.fields:
            if field.values:
                return options_of(field)
    return []


PARALLEL_UPDATES = 0  # the coordinator updates; the engine queues writes


async def async_setup_entry(
    hass: HomeAssistant, entry: EbusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    pending = coordinator.catalog.zones()

    # a zone is in use once one of its registers has a value; on a fresh install
    # nothing is cached yet, so keep checking as data arrives
    @callback
    def _add_active_zones() -> None:
        active = [z for z in pending if in_use(coordinator.data, z)]
        if active:
            for zone in active:
                pending.remove(zone)
            async_add_entities(EbusZoneClimate(coordinator, z) for z in active)

    _add_active_zones()
    if pending:
        entry.async_on_unload(coordinator.async_add_listener(_add_active_zones))


class EbusZoneClimate(EbusSettlingEntity, ClimateEntity):
    """One heating zone as a thermostat."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    # defaults; a zone takes its setpoint register's range (from the aioebus catalog)
    _attr_min_temp = 5.0
    _attr_max_temp = 30.0
    _attr_target_temperature_step = 0.5

    def __init__(self, coordinator: EbusCoordinator, zone: ClimateZone) -> None:
        super().__init__(coordinator)
        self._zone = zone
        zz = int(zone.zz, 16)
        self._attr_translation_key = "zone"  # also names the modes as the controller does
        self._attr_translation_placeholders = {"n": str(zone.n)}
        short = value_of(coordinator.data, zone.shortname)
        if isinstance(short, str) and short.strip():
            self._attr_name = short  # the name the user gave the zone on the controller
        self._attr_unique_id = zone_uid(coordinator.uid_prefix, zone.zz, zone.n)
        if zone.target_bounds is not None:  # the setpoint register's own range
            (self._attr_min_temp, self._attr_max_temp,
             self._attr_target_temperature_step) = zone.target_bounds
        self._attr_device_info = coordinator.device_info(zz)

        labels = _enum_labels(zone.mode_read) or _enum_labels(zone.mode_write)
        self._off = zone.modes.get("off")
        self._auto = zone.modes.get("auto")
        heat = zone.modes.get("heat")
        self._presets = [v for v in labels if v not in (self._off, self._auto, heat)]
        self._heat_default = heat or (self._presets[0] if self._presets else None)

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
        if zone.mode_write is not None and self._off:
            feature |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        # presets: the controller's further mode values, then away and airing
        self._functions = [p for p, ok in (
            (PRESET_AWAY, zone.holiday_start_write is not None
             and zone.holiday_end_write is not None and zone.holiday_end is not None),
            (PRESET_AIRING, zone.special_write is not None
             and {"ventilation", "none"} <= set(zone.special_values)),
        ) if ok]
        presets = (self._presets if zone.mode_write is not None else []) + self._functions
        if presets:
            self._attr_preset_modes = ([PRESET_NONE] if self._functions else []) + presets
            feature |= ClimateEntityFeature.PRESET_MODE
        self._attr_supported_features = feature

    def _data(self) -> EbusCache:
        return self.coordinator.data

    def _val(self, msg: MessageDef | None) -> object:
        return value_of(self._data(), msg)

    @property
    def current_temperature(self) -> float | None:
        try:
            return float(self._val(self._zone.current))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    @property
    def target_temperature(self) -> float | None:
        """The setpoint in effect now (time program, setback, quick veto),
        else the manual one; none while the zone is off."""
        if self.hvac_mode is HVACMode.OFF:
            return None
        for msg in (self._zone.setpoint, self._zone.target_read):
            try:
                return float(self._val(msg))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
        return None

    def _fixed_setpoint(self) -> float | None:
        """While airing the controller holds its setpoint (heating off): shown,
        but not adjustable."""
        return self.target_temperature if self._state() == AIRING else None

    @property
    def min_temp(self) -> float:
        fixed = self._fixed_setpoint()
        return fixed if fixed is not None else self._attr_min_temp

    @property
    def max_temp(self) -> float:
        fixed = self._fixed_setpoint()
        return fixed if fixed is not None else self._attr_max_temp

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
        state = self._state()
        if state == AWAY and PRESET_AWAY in self._functions:
            return PRESET_AWAY
        if state == AIRING and PRESET_AIRING in self._functions:
            return PRESET_AIRING
        value = self._val(self._zone.mode_read)
        if isinstance(value, str) and value in self._presets:
            return value
        return PRESET_NONE if self._functions else None

    def _state(self) -> str | None:
        return zone_state(self._data(), self._zone, dt_util.now())

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None and self._state() == AIRING:  # before anything is switched
            raise ServiceValidationError(
                f"eBUS zone {self._zone.n}: no setpoint while airing; end it first (preset)")
        mode = kwargs.get(ATTR_HVAC_MODE)  # set_temperature may switch too
        if mode is not None and mode != self.hvac_mode:
            await self.async_set_hvac_mode(mode)
        if temp is None:
            return
        zone = self._zone
        veto = zone.special_values.get("veto")
        if (self.hvac_mode is HVACMode.AUTO and zone.veto_temp is not None
                and zone.veto_duration is not None and zone.special_write is not None
                and veto):
            # the time program sets the setpoint: override it for a while (the
            # setpoint, the duration, then the special function that starts it)
            await self._write(zone.veto_temp, temp)
            await self._write(zone.veto_duration, self._veto_hours())
            await self._write(zone.special_write, veto)
        elif zone.target_write is not None:
            await self._write(zone.target_write, temp)
        else:
            return
        await self._read_effect()

    def _veto_hours(self) -> float:
        """The quick veto duration the controller has, else 3 h; within the
        register's range."""
        msg = self._zone.veto_duration
        if msg is None:
            return _VETO_HOURS
        catalog = self.coordinator.catalog
        hours = self._val(catalog.read_twin(msg))
        value = float(hours) if isinstance(hours, int | float) and hours > 0 else _VETO_HOURS
        bounds = catalog.bounds(catalog.owner(msg), msg.name)
        return min(max(value, bounds[0]), bounds[1]) if bounds else value

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        label = {
            HVACMode.OFF: self._off,
            HVACMode.AUTO: self._auto,
            HVACMode.HEAT: self._heat_default,
        }.get(hvac_mode)
        if label:
            await self._write_mode(label)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode in self._presets:
            await self._write_mode(preset_mode)
            return
        if preset_mode != PRESET_NONE and preset_mode not in self._functions:
            return
        zone, state, today = self._zone, self._state(), dt_util.now().date()
        if state == AWAY and preset_mode != PRESET_AWAY:
            await self._set_away(today - timedelta(days=1))  # a period that has passed
        if state == AIRING and preset_mode != PRESET_AIRING and zone.special_write:
            await self._write(zone.special_write, zone.special_values["none"])
        if preset_mode == PRESET_AWAY and state != AWAY:
            await self._set_away(today, today + timedelta(days=_AWAY_DAYS))
        if preset_mode == PRESET_AIRING and state != AIRING and zone.special_write:
            await self._write(zone.special_write, zone.special_values["ventilation"])
        await self._read_effect()

    async def _set_away(self, start: date, end: date | None = None) -> None:
        """Write the away period (start first: the end never lies before it)."""
        zone = self._zone
        if zone.holiday_start_write is None or zone.holiday_end_write is None:
            return
        await self._write(zone.holiday_start_write, start.strftime("%d.%m.%Y"))
        await self._write(zone.holiday_end_write, (end or start).strftime("%d.%m.%Y"))

    async def async_turn_off(self) -> None:
        if self._off:
            await self._write_mode(self._off)

    async def async_turn_on(self) -> None:
        if self._heat_default:
            await self._write_mode(self._heat_default)

    async def _write_mode(self, label: str) -> None:
        if self._zone.mode_write is not None:
            await self._write(self._zone.mode_write, label)
            await self._read_effect()

    async def _read_effect(self) -> None:
        """Read what a change set in effect, so the thermostat and the zone's
        state show it at once; the setpoint again once the controller has
        recalculated it."""
        zone = self._zone
        await self._read_change(
            (zone.setpoint, zone.special_read, zone.holiday_start, zone.holiday_end),
            (zone.setpoint,))

    async def _write(self, msg: MessageDef, value: object) -> None:
        await self.coordinator.async_write(msg, value)
