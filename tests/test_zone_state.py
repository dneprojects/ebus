"""What applies in a heating zone now (zone_state), and its sensor."""
import asyncio
from datetime import datetime

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from aioebus.catalog import Catalog
from aioebus.scan import ScanResult
from aioebus.values import values_of

from custom_components.ebus import zone_state as zs

_MON_7 = datetime(2026, 11, 2, 7, 0)  # a Monday, 07:00
_MON_23 = datetime(2026, 11, 2, 23, 0)
_WED_7 = datetime(2026, 11, 4, 7, 0)


@pytest.fixture(scope="module")
def zone():
    catalog = Catalog()
    asyncio.run(catalog.load({0x15: ScanResult(0xB5, "Vaillant", "CTLV3", "0808", "8004",
                                               808, 8004)}))
    return next(z for z in catalog.zones() if z.n == 1)


def _put(data, msg, value, field=None):
    vals = [v for v in values_of(msg) if field is None or v.field.name == field]
    data[vals[0].cache_key] = value


def _data(zone, mode="auto", special="auto", setback="normal", slots=1,
          start="06:00", end="22:00", holiday=("01.01.2019", "01.01.2019")):
    data: dict = {}
    _put(data, zone.mode_read, mode)
    if special is not None:
        _put(data, zone.special_read, special)
    if setback is not None:
        _put(data, zone.setback_mode, setback)
    for day in range(7):
        _put(data, zone.timer_frames, slots, "slotcount" if day == 0 else f"slotcount_{day}")
        _put(data, zone.timer_days[day], start, "htm")
        _put(data, zone.timer_days[day], end, "htm_1")
    _put(data, zone.holiday_start, holiday[0])
    _put(data, zone.holiday_end, holiday[1])
    return data


@pytest.mark.parametrize(("kwargs", "now", "state"), [
    ({"mode": "off"}, _MON_7, zs.OFF),
    ({"mode": "manual"}, _MON_7, zs.MANUAL),
    ({}, _MON_7, zs.TIME_SLOT),                         # 06:00-22:00
    ({}, _MON_23, zs.SETBACK),                          # outside, Normal
    ({"setback": "eco"}, _MON_23, zs.ECO),              # outside, Eco
    ({"setback": None}, _MON_23, zs.TIME_CONTROLLED),   # setback mode not read
    ({"setback": "other"}, _MON_23, zs.TIME_CONTROLLED),  # a value of no role
    ({"slots": 0}, _MON_7, zs.SETBACK),                 # no slot today
    ({"slots": 2}, _MON_7, zs.TIME_SLOT),               # in the first of two slots
    ({"slots": 2}, _MON_23, zs.TIME_CONTROLLED),        # only the first slot is known
    ({"start": "06:00", "end": "07:00"}, _MON_7, zs.SETBACK),   # end is exclusive
    ({"start": "07:00", "end": "24:00"}, _MON_23, zs.TIME_SLOT),  # until midnight
    ({"special": "veto"}, _MON_23, zs.QUICK_VETO),
    ({"special": "veto", "mode": "manual"}, _MON_7, zs.QUICK_VETO),
    ({"special": "ventilation"}, _MON_7, zs.AIRING),
    ({"special": "party"}, _MON_7, zs.SPECIAL),
    ({"special": None}, _MON_7, zs.TIME_SLOT),          # special function not read
    ({"holiday": ("01.11.2026", "05.11.2026")}, _WED_7, zs.AWAY),
    ({"holiday": ("01.11.2026", "04.11.2026")}, _WED_7, zs.AWAY),  # the last day counts
    ({"holiday": ("01.11.2026", "03.11.2026")}, _WED_7, zs.TIME_SLOT),
    ({"holiday": ("01.11.2026", "05.11.2026"), "mode": "off"}, _WED_7, zs.OFF),
])
def test_zone_state(zone, kwargs, now, state):
    assert zs.zone_state(_data(zone, **kwargs), zone, now) == state


def test_zone_state_unknown_until_the_mode_is_read(zone):
    assert zs.zone_state({}, zone, _MON_7) is None


def test_a_broken_slot_time_means_not_known(zone):
    assert zs.zone_state(_data(zone, start="xx"), zone, _MON_7) == zs.TIME_CONTROLLED
    assert zs.zone_state(_data(zone, end="25:00"), zone, _MON_7) == zs.TIME_CONTROLLED


def test_the_slot_of_the_weekday_counts(zone):
    data = _data(zone)
    _put(data, zone.timer_days[2], "08:00", "htm")  # Wednesday starts later
    assert zs.zone_state(data, zone, _WED_7) == zs.SETBACK
    assert zs.zone_state(data, zone, _MON_7) == zs.TIME_SLOT
    data = _data(zone)
    _put(data, zone.timer_frames, 0, "slotcount_2")  # no slot on Wednesday
    assert zs.zone_state(data, zone, _WED_7) == zs.SETBACK
    assert zs.zone_state(data, zone, _MON_7) == zs.TIME_SLOT


def test_a_field_the_message_lacks_has_no_value(zone):
    data = _data(zone)
    assert zs.value_of(data, zone.mode_read, "nosuchfield") is None
    assert zs.value_of(data, zone.mode_read) == "auto"


def test_a_zone_without_a_time_program_is_time_controlled():
    from dataclasses import replace
    catalog = Catalog()
    asyncio.run(catalog.load({0x15: ScanResult(0xB5, "Vaillant", "CTLV3", "0808", "8004",
                                               808, 8004)}))
    zone = replace(next(z for z in catalog.zones() if z.n == 1), timer_frames=None,
                   timer_days=(), setback_mode=None, special_read=None, holiday_start=None)
    data: dict = {}
    _put(data, zone.mode_read, "auto")
    assert zs.zone_state(data, zone, _MON_7) == zs.TIME_CONTROLLED
    assert zs.messages_of(zone) == [zone.mode_read, zone.holiday_end]


def test_every_state_is_translated():
    from custom_components.ebus.strings_gen import FIXED_STATES
    for lang, labels in FIXED_STATES["sensor"]["zone_state"].items():
        assert set(labels) == set(zs.STATES), lang


@pytest.fixture
def _enable(enable_custom_integrations):
    yield


async def test_the_state_sensor_shows_the_zone_state(hass, _enable):
    from unittest.mock import AsyncMock

    from aioebus.engine import EbusEngine
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.ebus.const import DOMAIN
    from custom_components.ebus.coordinator import EbusCoordinator
    from custom_components.ebus.sensor import EbusZoneStateSensor

    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            version=1, minor_version=3)
    entry.add_to_hass(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    coord.update_interval = None
    coord.devices = {0x15: ScanResult(0xB5, "Vaillant", "CTLV3", "0808", "8004", 808, 8004)}
    await coord.async_load_definitions()
    coord.engine = EbusEngine()
    coord.engine.read = AsyncMock()  # type: ignore[method-assign]
    zone2 = next(z for z in coord.catalog.zones() if z.n == 2)
    coord.data = {}
    sensor = EbusZoneStateSensor(coord, zone2)
    assert sensor.unique_id.endswith("_15_z2_state")
    assert sensor.translation_key == "zone_state"
    assert sensor.translation_placeholders == {"n": "2"}
    assert sensor.options == zs.STATES
    assert sensor.native_value is None  # mode not read yet
    coord.data = _data(zone2, mode="manual")
    assert sensor.native_value == zs.MANUAL
    coord.data = _data(zone2, mode="off")
    assert sensor.native_value == zs.OFF
