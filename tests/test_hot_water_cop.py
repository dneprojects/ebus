"""The hot water entity and a heat pump's COP (run under
pytest-homeassistant-custom-component; skipped where Home Assistant is not
installed)."""
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from aioebus.engine import EbusEngine
from aioebus.scan import ScanResult
from aioebus.values import value_key, values_of
from homeassistant.components.water_heater import WaterHeaterEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ebus import water_heater as platform
from custom_components.ebus.const import CONF_ALLOW_WRITE, DOMAIN
from custom_components.ebus.coordinator import EbusCoordinator
from custom_components.ebus.entities import cop_of, cop_sources, select_polled
from custom_components.ebus.ids import cop_uid, hot_water_uid
from custom_components.ebus.sensor import EbusCopSensor
from custom_components.ebus.water_heater import EbusHotWater

_DEVICES = {0x15: ScanResult(0xB5, "Vaillant", "CTLV3", "0808", "8004", 808, 8004),
            0x08: ScanResult(0xB5, "Vaillant", "HMU00", "0808", "5103", 808, 5103),
            0x18: ScanResult(0xB5, "Vaillant", "V32", "0808", "6004", 808, 6004),
            0x38: ScanResult(0xB5, "Vaillant", "V32", "0808", "9802", 808, 9802)}


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


async def _coordinator(hass: HomeAssistant, allow_write: bool = True) -> EbusCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            options={CONF_ALLOW_WRITE: allow_write}, version=1, minor_version=3)
    entry.add_to_hass(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    coord.update_interval = None
    coord.devices = dict(_DEVICES)
    await coord.async_load_definitions()
    coord.engine = EbusEngine(allow_write=True)
    coord.engine.write = AsyncMock()  # type: ignore[method-assign]
    coord.engine.read = AsyncMock()  # type: ignore[method-assign]
    coord.data = {}
    return coord


def _data(water, mode="auto", special="auto", target=50.0, current=47.25) -> dict:
    def key(msg):
        return value_key(msg, msg.name)
    return {key(water.mode_read): mode, key(water.special_read): special,
            key(water.target_read): target, key(water.current): current}


async def _entity(hass: HomeAssistant, allow_write: bool = True):
    coord = await _coordinator(hass, allow_write)
    [water] = coord.catalog.hot_water()
    entity = EbusHotWater(coord, water)
    entity.hass = hass
    written: list = []
    coord.async_write = AsyncMock(
        side_effect=lambda msg, value: written.append((msg.name, value)))
    return coord, water, entity, written


async def test_shows_what_the_controller_has(hass: HomeAssistant):
    coord, water, entity, _ = await _entity(hass)
    assert entity.operation_list == ["off", "auto", "manual", "boost"]
    assert entity.current_operation is None and entity.target_temperature is None
    assert entity.current_temperature is None  # nothing read yet
    coord.data = _data(water)
    assert (entity.current_operation, entity.target_temperature,
            entity.current_temperature) == ("auto", 50.0, 47.25)
    coord.data = _data(water, mode="day")
    assert entity.current_operation == "manual"  # the app's name for the register's "day"
    coord.data = _data(water, mode="off")
    assert entity.current_operation == "off"
    coord.data = _data(water, mode="auto", special="load")
    assert entity.current_operation == "boost"  # a charge shows whatever the mode
    coord.data = _data(water, mode="night")
    assert entity.current_operation is None  # a value without a role is not guessed


@pytest.mark.parametrize(("mode", "special", "operation", "writes"), [
    ("auto", "auto", "manual", [("HwcOpMode", "day")]),
    ("day", "auto", "auto", [("HwcOpMode", "auto")]),
    ("auto", "auto", "off", [("HwcOpMode", "off")]),
    ("auto", "auto", "auto", []),  # already so
    ("auto", "auto", "boost", [("HwcSFMode", "load")]),
    ("auto", "load", "boost", []),  # already charging
    ("auto", "load", "auto", [("HwcSFMode", "auto")]),  # ends the charge, keeps the mode
    ("auto", "load", "off", [("HwcSFMode", "auto"), ("HwcOpMode", "off")]),
    ("auto", "auto", "party", []),  # no such operation
])
async def test_operations_write_only_what_changes(hass: HomeAssistant, mode, special,
                                                  operation, writes):
    coord, water, entity, written = await _entity(hass)
    coord.data = _data(water, mode=mode, special=special)
    await entity.async_set_operation_mode(operation)
    assert written == writes


async def test_setpoint_is_written_and_read_back(hass: HomeAssistant):
    coord, water, entity, written = await _entity(hass)
    coord.data = _data(water)
    await entity.async_set_temperature(temperature=52.0)
    assert written == [("HwcTempDesired", 52.0)]
    assert coord.engine.read.await_args.args[0] is water.target_read
    written.clear()
    await entity.async_set_temperature()  # no temperature given
    assert written == []


async def test_a_change_is_read_again_once_settled(hass: HomeAssistant):
    coord, water, entity, _ = await _entity(hass)
    coord.data = _data(water)

    def reads(msg) -> int:
        return sum(c.args[0] is msg for c in coord.engine.read.await_args_list)

    await entity.async_set_operation_mode("boost")
    assert reads(water.special_read) == 1
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()
    assert reads(water.special_read) == 2


async def test_read_only_offers_nothing(hass: HomeAssistant):
    coord, water, entity, _ = await _entity(hass, allow_write=False)
    assert entity.supported_features == WaterHeaterEntityFeature(0)
    assert entity.operation_list  # still names what it shows
    writable = (await _entity(hass))[2]
    assert writable.supported_features == (WaterHeaterEntityFeature.TARGET_TEMPERATURE
                                           | WaterHeaterEntityFeature.OPERATION_MODE)


async def test_range_is_the_setpoint_registers(hass: HomeAssistant):
    _, water, entity, _ = await _entity(hass)
    assert (entity.min_temp, entity.max_temp) == water.target_bounds[:2]


async def test_without_the_special_function_there_is_no_charge(hass: HomeAssistant):
    from dataclasses import replace
    coord, water, _, written = await _entity(hass)
    entity = EbusHotWater(coord, replace(water, special_write=None))
    entity.hass = hass
    assert "boost" not in entity.operation_list
    coord.data = _data(water)
    await entity.async_set_operation_mode("boost")
    assert written == []


async def test_it_appears_once_a_register_has_a_value(hass: HomeAssistant):
    coord = await _coordinator(hass)
    [water] = coord.catalog.hot_water()
    entry = MagicMock(runtime_data=coord)
    added: list = []
    await platform.async_setup_entry(hass, entry, lambda new: added.extend(new))
    assert added == []
    entry.async_on_unload.assert_called_once()
    coord.data = _data(water)
    coord.async_update_listeners()
    assert [e.unique_id for e in added] == [hot_water_uid(coord.uid_prefix, "15")]
    coord.async_update_listeners()
    assert len(added) == 1  # only once


async def test_polled_while_enabled(hass: HomeAssistant):
    coord = await _coordinator(hass)
    [water] = coord.catalog.hot_water()
    uid = hot_water_uid(coord.uid_prefix, "15")
    derived = coord._derived_entities()
    assert {m.name for m in select_polled(coord.read_messages, {uid}, [], coord.uid_prefix,
                                          derived)} == {
        "HwcTempDesired", "HwcStorageTemp", "HwcOpMode", "HwcSFMode"}
    assert select_polled(coord.read_messages, set(), [], coord.uid_prefix, derived) == []


@pytest.mark.parametrize(("heat", "power", "cop"), [
    (6.477, 1.093, 5.93),
    (10.559, 2.173, 4.86),
    (0.0, 0.014, None),     # standby
    (0.0, 1.2, None),       # defrost: power, no heat
    (5.0, 0.014, None),     # a stale heat figure on standby power
    (5.0, 0.0, None),
    (None, 1.0, None),
    (5.0, None, None),
    ("x", 1.0, None),
])
def test_cop_of(heat, power, cop):
    assert cop_of(heat, power) == cop


async def test_each_heat_pump_has_a_cop(hass: HomeAssistant):
    coord = await _coordinator(hass)
    pumps = [a for a in coord.catalog.addresses
             if cop_sources(coord.catalog.messages(a)) is not None]
    assert sorted(pumps) == [0x08, 0x18]  # not the controller, not the eloBlock behind a coupler
    heat, power = cop_sources(coord.catalog.messages(0x18))
    sensor = EbusCopSensor(coord, 0x18, heat, power)
    assert sensor.unique_id == cop_uid(coord.uid_prefix, "18")
    assert sensor.native_value is None
    coord.data = {values_of(heat)[0].cache_key: 6.0, values_of(power)[0].cache_key: 1.2}
    assert sensor.native_value == 5.0
    # both registers stay polled while only the COP is enabled
    polled = select_polled(coord.read_messages, {sensor.unique_id}, [], coord.uid_prefix,
                           coord._derived_entities())
    assert {(m.name, m.zz) for m in polled} == {("RunDataHeatOutput", "18"),
                                                ("PowerConsumptionHmu", "18")}
