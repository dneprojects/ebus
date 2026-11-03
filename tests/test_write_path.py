"""Write path: coordinator.async_write, platforms, climate (run under
pytest-homeassistant-custom-component; skipped where Home Assistant is not
installed)."""
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from aioebus.active import ReadError, WriteUncertain
from aioebus.scan import ScanResult
from aioebus.values import value_key
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ebus.climate import EbusZoneClimate
from custom_components.ebus.const import CONF_ALLOW_WRITE, DOMAIN
from custom_components.ebus.coordinator import EbusCoordinator

_CTL = {0x15: ScanResult(0xB5, "Vaillant", "CTLV3", "0808", "8004", 808, 8004)}


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


async def _coordinator(hass: HomeAssistant, allow_write: bool = True) -> EbusCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            options={CONF_ALLOW_WRITE: allow_write}, version=1, minor_version=3)
    entry.add_to_hass(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    coord.update_interval = None
    coord.devices = dict(_CTL)
    await coord.async_load_definitions()
    coord.engine = MagicMock()
    coord.engine.cache = {}
    coord.engine.write = AsyncMock()
    coord.engine.read = AsyncMock()
    coord.data = {}
    return coord


def _write_msg(coord: EbusCoordinator, name: str):
    return next(m for m in coord.catalog.device_messages("w") if m.name == name)


async def test_write_reads_the_value_back(hass: HomeAssistant):
    coord = await _coordinator(hass)
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
    coord = await _coordinator(hass)
    msg = _write_msg(coord, "Z1DayTemp")
    coord.engine.read.side_effect = ReadError("no answer from slave")
    await coord.async_write(msg, 20.0)
    assert coord.data[value_key(msg, "Z1DayTemp")] == 20


async def test_unconfirmed_write_is_read_back_and_reported(hass: HomeAssistant):
    # sent but not confirmed: never retried; HA shows the read-back value and says
    # the write may have been applied (not a plain "failed")
    coord = await _coordinator(hass)
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
    coord = await _coordinator(hass)
    coord.engine.write.side_effect = err
    with pytest.raises(HomeAssistantError):
        await coord.async_write(_write_msg(coord, "Z1DayTemp"), 21.5)


async def test_write_refused_when_writing_is_off(hass: HomeAssistant):
    coord = await _coordinator(hass, allow_write=False)
    with pytest.raises(HomeAssistantError):
        await coord.async_write(_write_msg(coord, "Z1DayTemp"), 21.5)
    coord.engine.write.assert_not_awaited()


async def test_climate_setpoint_is_encodable(hass: HomeAssistant):
    # Z<n>DayTemp is an EXP float: encoding must work (it raised before 0.6)
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    assert zone.target_write is not None
    payload = zone.target_write.encode({zone.target_write.name: 21.5})
    assert payload.startswith(bytes.fromhex(zone.target_write.msgid))
    climate = EbusZoneClimate(coord, zone)
    climate.hass = hass
    await climate.async_set_temperature(temperature=21.5)
    coord.engine.write.assert_awaited_once()


async def test_climate_is_read_only_without_write_permission(hass: HomeAssistant):
    coord = await _coordinator(hass, allow_write=False)
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    climate = EbusZoneClimate(coord, zone)
    assert climate.supported_features == ClimateEntityFeature(0)
    assert climate.hvac_modes == [climate.hvac_mode]
    writable = EbusZoneClimate(await _coordinator(hass), zone)
    assert ClimateEntityFeature.TARGET_TEMPERATURE in writable.supported_features
    assert HVACMode.HEAT in writable.hvac_modes


async def test_climate_modes_write_the_zone_mode_register(hass: HomeAssistant):
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    climate = EbusZoneClimate(coord, zone)
    climate.hass = hass
    assert climate.hvac_modes == [HVACMode.OFF, HVACMode.HEAT, HVACMode.AUTO]
    assert climate.preset_modes == ["day", "night"]
    written = []
    coord.async_write = AsyncMock(side_effect=lambda msg, value: written.append((msg, value)))
    await climate.async_set_hvac_mode(HVACMode.OFF)
    await climate.async_set_hvac_mode(HVACMode.AUTO)
    await climate.async_set_hvac_mode(HVACMode.HEAT)  # heating at the comfort setpoint
    await climate.async_set_preset_mode("night")
    await climate.async_turn_off()
    await climate.async_turn_on()
    assert {m.name for m, _ in written} == {"Z1OpMode"}
    assert all(m.direction == "w" for m, _ in written)  # the active write, not the read
    assert [v for _, v in written] == ["off", "auto", "day", "night", "off", "day"]
    # the shown mode follows the read register's value
    for value, mode in (("off", HVACMode.OFF), ("auto", HVACMode.AUTO), ("night", HVACMode.HEAT)):
        coord.data = {value_key(zone.mode_read, zone.mode_read.name): value}
        assert climate.hvac_mode is mode
    assert climate.preset_mode == "night"


async def test_zone_name_is_translated_unless_the_user_named_it(hass: HomeAssistant):
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 2)
    plain = EbusZoneClimate(coord, zone)
    assert plain.translation_key == "zone" and plain.translation_placeholders == {"n": "2"}
    coord.data = {value_key(zone.shortname, zone.shortname.name): "Bad"}
    assert EbusZoneClimate(coord, zone).name == "Bad"


async def test_set_temperature_with_a_mode_switches_the_zone_too(hass: HomeAssistant):
    # climate.set_temperature may carry hvac_mode: an off zone must come on
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    climate = EbusZoneClimate(coord, zone)
    climate.hass = hass
    coord.data = {value_key(zone.mode_read, zone.mode_read.name): "off"}
    written = []
    coord.async_write = AsyncMock(side_effect=lambda msg, value: written.append((msg.name, value)))
    await climate.async_set_temperature(temperature=21.5, hvac_mode=HVACMode.HEAT)
    assert written == [("Z1OpMode", "day"), ("Z1DayTemp", 21.5)]


async def test_a_write_with_an_answer_field_writes_its_request_field(hass: HomeAssistant):
    from aioebus.messages import parse_csv
    coord = await _coordinator(hass)
    msg = parse_csv("type,circuit,level,name,comment,qq,zz,pbsb,id\n"
                    "w,,,SetX,,,15,b509,0e0100,value,m,UCH,,,,status,s,UCH,0=ok;1=err,,\n")[0]
    await coord.async_write(msg, 3)
    coord.engine.write.assert_awaited_once_with(msg, {"value": 3})


async def test_set_temperature_keeps_a_mode_already_on(hass: HomeAssistant):
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    climate = EbusZoneClimate(coord, zone)
    climate.hass = hass
    coord.data = {value_key(zone.mode_read, zone.mode_read.name): "night"}  # heating, night preset
    written = []
    coord.async_write = AsyncMock(side_effect=lambda msg, value: written.append((msg.name, value)))
    await climate.async_set_temperature(temperature=21.5, hvac_mode=HVACMode.HEAT)
    assert written == [("Z1DayTemp", 21.5)]
