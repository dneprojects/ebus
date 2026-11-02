"""Which writable registers become controls (run under
pytest-homeassistant-custom-component; skipped where Home Assistant is not
installed)."""
from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from aioebus.scan import Identification, ScanResult
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ebus import number, select, sensor
from custom_components.ebus.const import CONF_ALLOW_WRITE, DOMAIN
from custom_components.ebus.coordinator import EbusCoordinator
from custom_components.ebus.entities import bundled_control_class

_CTL = {0x15: ScanResult(0xB5, "Vaillant", "CTLV3", "0808", "8004", 808, 8004)}


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


async def _coordinator(hass: HomeAssistant) -> tuple[EbusCoordinator, MockConfigEntry]:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            options={CONF_ALLOW_WRITE: True}, version=1, minor_version=7)
    entry.add_to_hass(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    coord.update_interval = None
    coord.devices = dict(_CTL)
    await coord.async_load_definitions()
    coord.data = {}
    entry.runtime_data = coord
    return coord, entry


async def _entities(hass, platform):
    coord, entry = await _coordinator(hass)
    added: list = []
    await platform.async_setup_entry(hass, entry, lambda ents: added.extend(ents))
    return {e._msg.name: e for e in added}


async def test_counters_and_live_states_get_no_control(hass: HomeAssistant):
    nums = await _entities(hass, number)
    for name in ("PrEnergySumHc", "PrFuelSumHwcLastMonth", "YieldTotal", "SolarYieldTotal",
                 "Hc1Status", "Hc1PumpStatus", "Z1ValveStatus", "OutsideTempAvg"):
        assert name not in nums, name


async def test_installer_settings_are_disabled_configuration(hass: HomeAssistant):
    nums = await _entities(hass, number)
    for name in ("HydraulicScheme", "KeyCodeforConfigMenu", "PumpAdditionalTime",
                 "Hc1SummerTempLimit", "Hc2MinFlowTempDesired", "Hc3MaxFlowTempDesired",
                 "Hc1MinCoolingTempDesired"):
        e = nums[name]
        assert e.entity_category is EntityCategory.CONFIG
        assert e.entity_registry_enabled_default is False


async def test_float_setpoints_become_numbers_with_sane_ranges(hass: HomeAssistant):
    nums = await _entities(hass, number)
    hwc = nums["HwcTempDesired"]
    assert (hwc.native_min_value, hwc.native_max_value, hwc.native_step) == (35, 70, 1)
    assert hwc.entity_category is None and hwc.entity_registry_enabled_default is not False
    curve = nums["Hc1HeatCurve"]
    assert (curve.native_min_value, curve.native_max_value, curve.native_step) == (0.1, 4, 0.05)
    assert "Z1NightTemp" in nums
    day = nums["Z1DayTemp"]
    assert (day.native_min_value, day.native_max_value, day.native_step) == (5, 30, 0.5)
    assert day.entity_category is None


async def test_integer_settings_use_listed_ranges(hass: HomeAssistant):
    nums = await _entities(hass, number)
    lock = nums["HwcLockTime"]
    assert (lock.native_min_value, lock.native_max_value, lock.native_step) == (0, 120, 10)
    assert lock.entity_category is None


async def test_user_selects_stay_installer_selects_are_configuration(hass: HomeAssistant):
    sels = await _entities(hass, select)
    assert sels["HwcOpMode"].entity_category is None
    assert sels["Z1RoomZoneMapping"].entity_category is EntityCategory.CONFIG
    assert sels["MultiRelaySetting"].entity_registry_enabled_default is False


def test_bundled_class_lookup():
    assert bundled_control_class("PrEnergySumHc") == "none"
    assert bundled_control_class("HydraulicScheme") == "installer"
    assert bundled_control_class("HwcOpMode") == "user"
    assert bundled_control_class("SomethingUserDefined") is None


async def test_migration_disables_installer_controls_only(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            version=1, minor_version=4)
    entry.add_to_hass(hass)
    other = MockConfigEntry(domain=DOMAIN, data={"host": "5.6.7.8", "port": 9999},
                            version=1, minor_version=7)
    other.add_to_hass(hass)
    reg = er.async_get(hass)

    def add(domain, name, owner=entry, **kw):
        return reg.async_get_or_create(domain, DOMAIN, f"{owner.entry_id}_15_{name}_{name}_set",
                                       config_entry=owner, **kw).entity_id

    counter = add("number", "PrEnergySumHc")
    scheme = add("number", "HydraulicScheme")
    mapping = add("select", "Z1RoomZoneMapping",
                  disabled_by=er.RegistryEntryDisabler.USER)
    opmode = add("select", "HwcOpMode")
    own = add("number", "MyOwnRegister")  # only in a user definition
    foreign = add("number", "HydraulicScheme", owner=other)
    with patch("custom_components.ebus.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.minor_version == 7
    assert reg.async_get(scheme).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert reg.async_get(mapping).disabled_by is er.RegistryEntryDisabler.USER
    for kept in (counter, opmode, own, foreign):  # counters go at setup, by device
        assert reg.async_get(kept).disabled_by is None, kept


async def test_migration_1_6_switches_off_the_heating_circuit_limits_only(
        hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            version=1, minor_version=5)
    entry.add_to_hass(hass)
    reg = er.async_get(hass)

    def add(name, **kw):
        return reg.async_get_or_create("number", DOMAIN,
                                       f"{entry.entry_id}_15_{name}_{name}_set",
                                       config_entry=entry, **kw).entity_id

    limits = [add(f"Hc{n}{x}") for n in "13"
              for x in ("SummerTempLimit", "MinFlowTempDesired", "MaxFlowTempDesired")]
    scheme = add("HydraulicScheme")  # configuration the user switched on since 1.5
    curve = add("Hc1HeatCurve")
    with patch("custom_components.ebus.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.minor_version == 7
    for eid in limits:
        assert reg.async_get(eid).disabled_by is er.RegistryEntryDisabler.INTEGRATION, eid
    assert reg.async_get(scheme).disabled_by is None
    assert reg.async_get(curve).disabled_by is None


async def test_migration_1_7_switches_off_the_cooling_limit_only(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            version=1, minor_version=6)
    entry.add_to_hass(hass)
    reg = er.async_get(hass)

    def add(name):
        return reg.async_get_or_create("number", DOMAIN,
                                       f"{entry.entry_id}_15_{name}_{name}_set",
                                       config_entry=entry).entity_id

    cooling = [add(f"Hc{n}MinCoolingTempDesired") for n in "12"]
    limit = add("Hc1SummerTempLimit")  # switched on again by the user after 1.6
    with patch("custom_components.ebus.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.minor_version == 7
    for eid in cooling:
        assert reg.async_get(eid).disabled_by is er.RegistryEntryDisabler.INTEGRATION, eid
    assert reg.async_get(limit).disabled_by is None


async def test_setup_drops_controls_a_register_no_longer_gets(hass: HomeAssistant):
    coord, entry = await _coordinator(hass)
    reg = er.async_get(hass)
    stale = reg.async_get_or_create(
        "number", DOMAIN, f"{entry.entry_id}_15_PrEnergySumHc_PrEnergySumHc_set",
        config_entry=entry).entity_id
    sensor = reg.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_15_PrEnergySumHc_PrEnergySumHc",
        config_entry=entry).entity_id
    await number.async_setup_entry(hass, entry, lambda ents: None)
    assert reg.async_get(stale) is None
    assert reg.async_get(sensor) is not None  # the reading stays


async def test_setup_drops_a_select_a_register_no_longer_gets(hass: HomeAssistant):
    # every bundled select is classified today; a future reclassification to
    # "none" must clean up the same way
    coord, entry = await _coordinator(hass)
    reg = er.async_get(hass)
    stale = reg.async_get_or_create(
        "select", DOMAIN, f"{entry.entry_id}_15_HwcOpMode_HwcOpMode_set",
        config_entry=entry).entity_id
    with patch("custom_components.ebus.coordinator.control_class", return_value="none"):
        await select.async_setup_entry(hass, entry, lambda ents: None)
    assert reg.async_get(stale) is None


async def test_setup_keeps_controls_of_a_user_definition(hass: HomeAssistant):
    # a device without a reviewed class list (a user definition) keeps every control
    coord, entry = await _coordinator(hass)
    reg = er.async_get(hass)
    uid = f"{entry.entry_id}_15_PrEnergySumHc_PrEnergySumHc_set"
    kept = reg.async_get_or_create("number", DOMAIN, uid, config_entry=entry).entity_id
    with patch("custom_components.ebus.coordinator.control_class", return_value="installer"):
        nums = await _entities(hass, number)
    assert reg.async_get(kept) is not None
    assert nums["PrEnergySumHc"].entity_category is EntityCategory.CONFIG


async def test_identification_read_later_adds_its_sensors_once(hass: HomeAssistant):
    coord, entry = await _coordinator(hass)
    added: list = []
    await sensor.async_setup_entry(hass, entry, lambda ents: added.extend(ents))
    before = len(added)
    coord.idents[0x15] = Identification("0020260913", "21212300202609130000000000N1", 2021, 12)
    coord.async_update_listeners()
    once = len(added)
    coord.async_update_listeners()
    assert len(added) == once  # not added again on later updates
    uids = {e.unique_id for e in added[before:]}
    assert {coord.uid("15", "artikelnummer"), coord.uid("15", "seriennummer")} <= uids


async def test_switching_writes_off_drops_the_controls(hass: HomeAssistant):
    coord, entry = await _coordinator(hass)
    coord.allow_write = False
    coord.write_messages = []
    reg = er.async_get(hass)
    ctl = reg.async_get_or_create("number", DOMAIN, f"{entry.entry_id}_15_HwcLockTime_HwcLockTime_set",
                                  config_entry=entry).entity_id
    sel = reg.async_get_or_create("select", DOMAIN, f"{entry.entry_id}_15_HwcOpMode_HwcOpMode_set",
                                  config_entry=entry).entity_id
    reading = reg.async_get_or_create("sensor", DOMAIN, f"{entry.entry_id}_15_HwcOpMode_HwcOpMode",
                                      config_entry=entry).entity_id
    await number.async_setup_entry(hass, entry, lambda ents: None)
    await select.async_setup_entry(hass, entry, lambda ents: None)
    assert reg.async_get(ctl) is None and reg.async_get(sel) is None
    assert reg.async_get(reading) is not None


def test_integer_ranges_follow_the_type():
    from aioebus.messages import FieldDef

    from custom_components.ebus.number import _bounds

    def field(type_, divisor=None):
        return FieldDef("value", "", type_, divisor, None, "", "")

    assert _bounds(field("SCH")) == (-127, 127, 1)
    assert _bounds(field("SIN")) == (-32767, 32767, 1)
    assert _bounds(field("UCH", -10)) == (0, 2540, 10)  # a negative divisor multiplies
    assert _bounds(field("D1C")) == (0, 100.0, 0.5)


def test_bundled_class_lookup_takes_profile_keys():
    # a reviewed section may be keyed by an IDENT/HW profile: the migrations that
    # look a class up must resolve it to its file, not fail
    from custom_components.ebus import entities
    sections = {**entities.presentation()["controls"],
                "V32/6004": {"user": ["Hc1DayTemp"], "installer": []}}
    with patch.object(entities, "presentation",
                      return_value={**entities.presentation(), "controls": sections}):
        assert bundled_control_class("HwcOpMode") == "user"


async def test_a_register_sent_elsewhere_stays_on_its_device(hass: HomeAssistant, tmp_path):
    # a definition row may address another module (ebusd: a ZZ column): its
    # entity still belongs to the device whose definition holds it
    (tmp_path / "08.ehp.csv").write_text(
        "type,circuit,level,name,comment,qq,zz,pbsb,id,*name,part,type,divisor/values,unit,comment\n"
        "r,,,HwcTemp,,,25,b509,290000,value,,D2C,,°C,\n", encoding="utf-8")
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            options={"config_dir": str(tmp_path)}, version=1, minor_version=7)
    entry.add_to_hass(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    coord.devices = {0x08: ScanResult(0xB5, "Vaillant", "EHP00", "0100", "0200", 100, 200)}
    await coord.async_load_definitions()
    coord.data = {}
    from aioebus.values import values_of
    msg = next(m for m in coord.read_messages if m.name == "HwcTemp")
    assert msg.zz == "25"
    ent = sensor.EbusSensor(coord, values_of(msg)[0])
    assert ent.device_info["identifiers"] == {(DOMAIN, "08")}
