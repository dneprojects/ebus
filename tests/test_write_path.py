"""Write path: coordinator.async_write, platforms, climate (run under
pytest-homeassistant-custom-component; skipped where Home Assistant is not
installed)."""
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from aioebus.active import ReadError, WriteUncertain
from aioebus.engine import EbusEngine
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
    # the real write-and-read-back rule, with the bus calls replaced
    coord.engine = EbusEngine(allow_write=True)
    coord.engine.write = AsyncMock()  # type: ignore[method-assign]
    coord.engine.read = AsyncMock()  # type: ignore[method-assign]
    coord.data = {}
    return coord


def _write_msg(coord: EbusCoordinator, name: str):
    return next(m for m in coord.catalog.device_messages("w") if m.name == name)


async def test_write_reads_the_value_back(hass: HomeAssistant):
    coord = await _coordinator(hass)
    msg = _write_msg(coord, "Z1DayTemp")
    key = value_key(msg, "Z1DayTemp")

    async def read(twin, *_):
        assert twin.direction[:1] == "r" and twin.name == "Z1DayTemp"
        coord.engine.cache[key] = 21.0  # what the device actually took

    coord.engine.read.side_effect = read
    await coord.async_write(msg, 21.5)
    field = msg.fields[-1].name
    coord.engine.write.assert_awaited_once()
    assert coord.engine.write.await_args.args[:2] == (msg, {field: 21.5})
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

    async def read(_twin, *_):
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


async def test_climate_modes_are_the_controllers(hass: HomeAssistant):
    # sensoCOMFORT: time-controlled, manual, off -- no day/night presets
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    climate = EbusZoneClimate(coord, zone)
    climate.hass = hass
    assert climate.hvac_modes == [HVACMode.OFF, HVACMode.HEAT, HVACMode.AUTO]
    assert ClimateEntityFeature.PRESET_MODE not in climate.supported_features
    written = []
    coord.async_write = AsyncMock(side_effect=lambda msg, value: written.append((msg, value)))
    await climate.async_set_hvac_mode(HVACMode.OFF)
    await climate.async_set_hvac_mode(HVACMode.AUTO)
    await climate.async_set_hvac_mode(HVACMode.HEAT)  # manual heating
    await climate.async_turn_off()
    await climate.async_turn_on()
    assert {m.name for m, _ in written} == {"Z1OpMode"}
    assert all(m.direction == "w" for m, _ in written)  # the active write, not the read
    assert [v for _, v in written] == ["off", "auto", "manual", "off", "manual"]
    # the shown mode follows the read register's value
    for value, mode in (("off", HVACMode.OFF), ("auto", HVACMode.AUTO), ("manual", HVACMode.HEAT)):
        coord.data = {value_key(zone.mode_read, zone.mode_read.name): value}
        assert climate.hvac_mode is mode
    assert climate.preset_mode is None


def _zone_data(zone, mode=None, setpoint=None, manual=None) -> dict:
    data = {}
    for msg, value in ((zone.mode_read, mode), (zone.setpoint, setpoint),
                       (zone.target_read, manual)):
        if value is not None:
            data[value_key(msg, msg.name)] = value
    return data


async def test_climate_shows_the_setpoint_in_effect(hass: HomeAssistant):
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    climate = EbusZoneClimate(coord, zone)
    coord.data = _zone_data(zone, "auto", setpoint=18.0, manual=21.0)  # setback time
    assert climate.target_temperature == 18.0
    coord.data = _zone_data(zone, "auto", manual=21.0)  # not read yet: the manual one
    assert climate.target_temperature == 21.0


@pytest.mark.parametrize(("cached", "hours"), [(2.5, 2.5), (None, 3.0)])
async def test_set_temperature_in_auto_is_a_quick_veto(hass: HomeAssistant, cached, hours):
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    climate = EbusZoneClimate(coord, zone)
    climate.hass = hass
    coord.data = _zone_data(zone, "auto", setpoint=21.0)
    if cached is not None:
        twin = coord.catalog.read_twin(zone.veto_duration)
        coord.data[value_key(twin, twin.name)] = cached
    written = []
    coord.async_write = AsyncMock(side_effect=lambda msg, value: written.append((msg.name, value)))
    await climate.async_set_temperature(temperature=22.5)
    # the setpoint, the duration, then the special function that starts the veto
    assert written == [("Z1QuickVetoTemp", 22.5), ("Z1QuickVetoDuration", hours),
                       ("Z1SFMode", "veto")]
    # what it set in effect is shown at once
    assert [c.args[0] for c in coord.engine.read.await_args_list] == [
        zone.setpoint, zone.special_read]


@pytest.mark.parametrize(("cached", "hours"), [(20.0, 12.0), (0.2, 0.5)])
async def test_the_veto_duration_stays_in_range(hass: HomeAssistant, cached, hours):
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    climate = EbusZoneClimate(coord, zone)
    climate.hass = hass
    coord.data = _zone_data(zone, "auto", setpoint=21.0)
    twin = coord.catalog.read_twin(zone.veto_duration)
    coord.data[value_key(twin, twin.name)] = cached
    written = []
    coord.async_write = AsyncMock(side_effect=lambda msg, value: written.append((msg.name, value)))
    await climate.async_set_temperature(temperature=22.0)
    assert ("Z1QuickVetoDuration", hours) in written


async def test_without_the_special_function_auto_sets_the_manual_setpoint(hass: HomeAssistant):
    # a declaration without the special function cannot start a veto: no half veto
    from dataclasses import replace
    coord = await _coordinator(hass)
    zone = replace(next(z for z in coord.catalog.zones() if z.n == 1), special_write=None)
    climate = EbusZoneClimate(coord, zone)
    climate.hass = hass
    coord.data = _zone_data(zone, "auto", setpoint=21.0)
    written = []
    coord.async_write = AsyncMock(side_effect=lambda msg, value: written.append((msg.name, value)))
    await climate.async_set_temperature(temperature=22.0)
    assert written == [("Z1DayTemp", 22.0)]


async def test_read_now_without_an_engine_does_nothing(hass: HomeAssistant):
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    coord.engine = None
    coord.async_set_updated_data = MagicMock()
    await coord.async_read_now(zone.setpoint)
    coord.async_set_updated_data.assert_not_called()


async def test_set_temperature_with_auto_starts_a_quick_veto(hass: HomeAssistant):
    # one call that switches to auto and sets a temperature: the veto, not manual
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    climate = EbusZoneClimate(coord, zone)
    climate.hass = hass
    coord.data = _zone_data(zone, "manual", setpoint=21.0)

    async def write(msg, value):
        written.append((msg.name, value))
        if msg is zone.mode_write:  # the device took the mode (cached on write)
            key = value_key(zone.mode_read, zone.mode_read.name)
            coord.data[key] = coord.engine.cache[key] = value

    written = []
    coord.async_write = AsyncMock(side_effect=write)
    await climate.async_set_temperature(temperature=22.0, hvac_mode=HVACMode.AUTO)
    assert [n for n, _ in written] == ["Z1OpMode", "Z1QuickVetoTemp", "Z1QuickVetoDuration",
                                       "Z1SFMode"]


async def test_a_failed_veto_write_starts_nothing(hass: HomeAssistant):
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    climate = EbusZoneClimate(coord, zone)
    climate.hass = hass
    coord.data = _zone_data(zone, "auto", setpoint=21.0)
    written = []

    async def write(msg, value):
        if msg is zone.veto_duration:
            raise HomeAssistantError("no answer")
        written.append(msg.name)

    coord.async_write = AsyncMock(side_effect=write)
    with pytest.raises(HomeAssistantError):
        await climate.async_set_temperature(temperature=22.0)
    assert written == ["Z1QuickVetoTemp"]  # the special function is not started


async def test_an_off_zone_shows_no_setpoint(hass: HomeAssistant):
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    climate = EbusZoneClimate(coord, zone)
    climate.hass = hass
    coord.data = _zone_data(zone, "off", setpoint=5.0, manual=21.0)
    assert climate.target_temperature is None
    written = []
    coord.async_write = AsyncMock(side_effect=lambda msg, value: written.append((msg.name, value)))
    await climate.async_set_temperature(temperature=20.0)
    assert written == [("Z1DayTemp", 20.0)]  # kept for when it heats manually


async def test_a_mode_change_reads_what_it_set_in_effect(hass: HomeAssistant):
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    climate = EbusZoneClimate(coord, zone)
    climate.hass = hass
    coord.async_write = AsyncMock()
    await climate.async_set_hvac_mode(HVACMode.AUTO)
    assert [c.args[0] for c in coord.engine.read.await_args_list] == [
        zone.setpoint, zone.special_read]


async def test_set_temperature_while_manual_sets_the_manual_setpoint(hass: HomeAssistant):
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    climate = EbusZoneClimate(coord, zone)
    climate.hass = hass
    coord.data = _zone_data(zone, "manual", setpoint=21.0)
    written = []
    coord.async_write = AsyncMock(side_effect=lambda msg, value: written.append((msg.name, value)))
    await climate.async_set_temperature(temperature=20.0)
    assert written == [("Z1DayTemp", 20.0)]


async def test_setpoint_read_failure_after_a_write_is_quiet(hass: HomeAssistant):
    coord = await _coordinator(hass)
    coord.engine.read.side_effect = ReadError("no answer from slave")
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    coord.async_set_updated_data = MagicMock()
    await coord.async_read_now(zone.setpoint)  # the next poll reads it
    await coord.async_read_now(None)
    coord.async_set_updated_data.assert_not_called()


async def test_read_now_shows_what_it_read(hass: HomeAssistant):
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    key = value_key(zone.setpoint, zone.setpoint.name)

    async def read(msg, *_):
        if msg is zone.special_read:
            raise TimeoutError  # one failing read does not stop the others
        coord.engine.cache[key] = 18.0

    coord.engine.read.side_effect = read
    await coord.async_read_now(zone.special_read, zone.setpoint)
    assert coord.data[key] == 18.0


async def test_zone_name_is_translated_unless_the_user_named_it(hass: HomeAssistant):
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 2)
    plain = EbusZoneClimate(coord, zone)
    assert plain.translation_key == "zone" and plain.translation_placeholders == {"n": "2"}
    coord.data = {value_key(zone.shortname, zone.shortname.name): "Bad"}
    named = EbusZoneClimate(coord, zone)
    assert named.name == "Bad"
    assert named.translation_key == "zone"  # its modes still carry the controller's names


def test_zone_modes_are_named_as_the_controller_does():
    from custom_components.ebus.strings_gen import FIXED_STATES
    assert FIXED_STATES["climate"]["zone"]["de"] == {
        "auto": "Zeitgesteuert", "heat": "Manuell", "off": "Aus"}
    assert set(FIXED_STATES["climate"]["zone"]["en"]) == {"auto", "heat", "off"}


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
    assert written == [("Z1OpMode", "manual"), ("Z1DayTemp", 21.5)]


async def test_a_write_with_an_answer_field_writes_its_request_field(hass: HomeAssistant):
    from aioebus.messages import parse_csv
    coord = await _coordinator(hass)
    msg = parse_csv("type,circuit,level,name,comment,qq,zz,pbsb,id\n"
                    "w,,,SetX,,,15,b509,0e0100,value,m,UCH,,,,status,s,UCH,0=ok;1=err,,\n")[0]
    await coord.async_write(msg, 3)
    assert coord.engine.write.await_args.args[:2] == (msg, {"value": 3})


async def test_set_temperature_keeps_a_mode_already_on(hass: HomeAssistant):
    coord = await _coordinator(hass)
    zone = next(z for z in coord.catalog.zones() if z.n == 1)
    climate = EbusZoneClimate(coord, zone)
    climate.hass = hass
    coord.data = {value_key(zone.mode_read, zone.mode_read.name): "manual"}  # heating already
    written = []
    coord.async_write = AsyncMock(side_effect=lambda msg, value: written.append((msg.name, value)))
    await climate.async_set_temperature(temperature=21.5, hvac_mode=HVACMode.HEAT)
    assert written == [("Z1DayTemp", 21.5)]
