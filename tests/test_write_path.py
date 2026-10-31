"""Write path: coordinator.async_write, platforms, climate (run under
pytest-homeassistant-custom-component; skipped where Home Assistant is not
installed)."""
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from aioebus.active import ReadError, WriteUncertain
from aioebus.engine import value_key
from aioebus.scan import ScanResult
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ebus.climate import EbusZoneClimate
from custom_components.ebus.const import CONF_ALLOW_WRITE, DOMAIN
from custom_components.ebus.coordinator import EbusCoordinator
from custom_components.ebus.library import climate_zones, device_messages

_CTL = {0x15: ScanResult(0xB5, "Vaillant", "CTLV3", "0808", "8004", 808, 8004)}


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


def _coordinator(hass: HomeAssistant, allow_write: bool = True) -> EbusCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            options={CONF_ALLOW_WRITE: allow_write}, version=1, minor_version=3)
    entry.add_to_hass(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    coord.update_interval = None
    coord.devices = dict(_CTL)
    coord.read_messages = device_messages(coord.devices, "r")
    coord.write_messages = device_messages(coord.devices, "w") if allow_write else []
    coord.engine = MagicMock()
    coord.engine.cache = {}
    coord.engine.write = AsyncMock()
    coord.engine.read = AsyncMock()
    coord.data = {}
    return coord


def _write_msg(coord: EbusCoordinator, name: str):
    return next(m for m in device_messages(coord.devices, "w") if m.name == name)


async def test_write_reads_the_value_back(hass: HomeAssistant):
    coord = _coordinator(hass)
    msg = _write_msg(coord, "Z1DayTemp")
    key = value_key(msg, "Z1DayTemp")

    async def read(twin):
        assert twin.direction[:1] == "r" and twin.name == "Z1DayTemp"
        coord.engine.cache[key] = 21.0  # what the device actually took

    coord.engine.read.side_effect = read
    await coord.async_write(msg, 21.5)
    field = msg.fields[-1].name
    coord.engine.write.assert_awaited_once_with(msg, {field: 21.5})
    assert coord.data[key] == 21.0  # the device's value, not the requested one


async def test_write_without_read_back_shows_the_written_value(hass: HomeAssistant):
    coord = _coordinator(hass)
    msg = _write_msg(coord, "Z1DayTemp")
    coord.engine.read.side_effect = ReadError("no answer from slave")
    await coord.async_write(msg, 20.0)
    assert coord.data[value_key(msg, "Z1DayTemp")] == 20


async def test_unconfirmed_write_is_read_back_and_reported(hass: HomeAssistant):
    # sent but not confirmed: never retried; HA shows the read-back value and says
    # the write may have been applied (not a plain "failed")
    coord = _coordinator(hass)
    msg = _write_msg(coord, "Z1DayTemp")
    key = value_key(msg, "Z1DayTemp")
    coord.engine.write.side_effect = WriteUncertain("collision: echo mismatch")

    async def read(_twin):
        coord.engine.cache[key] = 21.5  # the device did take it

    coord.engine.read.side_effect = read
    with pytest.raises(HomeAssistantError, match="may have been applied"):
        await coord.async_write(msg, 21.5)
    coord.engine.write.assert_awaited_once()
    assert coord.data[key] == 21.5

    coord.engine.read.side_effect = ReadError("no answer from slave")
    coord.engine.cache[key] = 20.0
    with pytest.raises(HomeAssistantError, match="could not be read back"):
        await coord.async_write(msg, 21.5)
    assert coord.data[key] == 20.0  # unconfirmed: no optimistic value


@pytest.mark.parametrize("err", [ReadError("command NAKed by slave"), TimeoutError(),
                                 ValueError("out of range"), NotImplementedError("STR")])
async def test_write_errors_become_ha_errors(hass: HomeAssistant, err):
    coord = _coordinator(hass)
    coord.engine.write.side_effect = err
    with pytest.raises(HomeAssistantError):
        await coord.async_write(_write_msg(coord, "Z1DayTemp"), 21.5)


async def test_write_refused_when_writing_is_off(hass: HomeAssistant):
    coord = _coordinator(hass, allow_write=False)
    with pytest.raises(HomeAssistantError):
        await coord.async_write(_write_msg(coord, "Z1DayTemp"), 21.5)
    coord.engine.write.assert_not_awaited()


async def test_climate_setpoint_is_encodable(hass: HomeAssistant):
    # Z<n>DayTemp is an EXP float: encoding must work (it raised before 0.6)
    coord = _coordinator(hass)
    zone = next(z for z in climate_zones(coord.devices) if z.n == 1)
    assert zone.target_write is not None
    payload = zone.target_write.encode({zone.target_write.name: 21.5})
    assert payload.startswith(bytes.fromhex(zone.target_write.msgid))
    climate = EbusZoneClimate(coord, zone)
    climate.hass = hass
    await climate.async_set_temperature(temperature=21.5)
    coord.engine.write.assert_awaited_once()


async def test_climate_is_read_only_without_write_permission(hass: HomeAssistant):
    coord = _coordinator(hass, allow_write=False)
    zone = next(z for z in climate_zones(coord.devices) if z.n == 1)
    climate = EbusZoneClimate(coord, zone)
    assert climate.supported_features == ClimateEntityFeature(0)
    assert climate.hvac_modes == [climate.hvac_mode]
    writable = EbusZoneClimate(_coordinator(hass), zone)
    assert ClimateEntityFeature.TARGET_TEMPERATURE in writable.supported_features
    assert HVACMode.HEAT in writable.hvac_modes
