"""Config entry migrations: each step brings the registry of an older entry up
to date once (run under pytest-homeassistant-custom-component; skipped where
Home Assistant is not installed)."""
from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ebus.const import DOMAIN
from custom_components.ebus.device import ADAPTER_IDENTIFIER
from custom_components.ebus.ids import message_of

_DATA = {"host": "1.2.3.4", "port": 9999}


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


def _entry(minor_version: int = 2) -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, data=_DATA, version=1, minor_version=minor_version)


def test_message_name_from_an_entity_id():
    assert message_of("01J_15_Z1DayTemp_Z1DayTemp") == "Z1DayTemp"
    assert message_of("01J_15_Z1DayTemp_Z1DayTemp_set") == "Z1DayTemp"
    assert message_of("01J_15_HwcTimer_Monday0_HwcTimer_Monday0 from") == "HwcTimer_Monday0"
    assert message_of("01J_15_Hc1_Hc1 x_set") == "Hc1"
    assert message_of("01J_adapter_signal") is None
    assert message_of("01J_15") is None


async def test_migration_drops_retired_and_moves_renamed(hass: HomeAssistant):
    entry = _entry(minor_version=1)
    entry.add_to_hass(hass)
    reg = er.async_get(hass)

    def add(uid: str) -> str:
        return reg.async_get_or_create("sensor", DOMAIN, uid, config_entry=entry).entity_id

    renamed = add("ebus_08_EnergyYieldHeat_EnergyYieldHeat")
    retired = add("ebus_18_YieldHc_YieldHc")
    orphan = add("ebus_18_SupplyTempWeighted_SupplyTempWeighted")
    successor = add("ebus_18_TargetFlowTemp_TargetFlowTemp")
    kept = add("ebus_08_Status01_Status01 temp")

    with patch("custom_components.ebus.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)

    assert entry.minor_version == 7
    # the renamed counter keeps its entity_id (and so its history)
    assert reg.async_get(renamed).unique_id == (
        f"{entry.entry_id}_08_EnergyGeneratedHc_EnergyGeneratedHc")
    assert reg.async_get(retired) is None
    assert reg.async_get(orphan) is None  # its successor already existed
    assert reg.async_get(successor) is not None
    assert reg.async_get(kept) is not None


async def test_migration_suffixes_nameless_entity_ids(hass: HomeAssistant):
    # a 'uw' sensor registered without a name got the bare (area-prefixed) device
    # slug as entity_id; 1.3 appends its label, other ids stay untouched
    entry = _entry(minor_version=2)
    entry.add_to_hass(hass)
    area = ar.async_get(hass).async_create("Haustechnik")
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "08")}, name="Wärmepumpe")
    dr.async_get(hass).async_update_device(
        dev.id, name_by_user="aroTHERM plus 0", area_id=area.id)
    reg = er.async_get(hass)
    bare = reg.async_get_or_create(
        "sensor", DOMAIN, "ebus_08_StatusCirPump_StatusCirPump", config_entry=entry,
        device_id=dev.id, suggested_object_id="haustechnik_arotherm_plus_0")
    named = reg.async_get_or_create(
        "sensor", DOMAIN, "ebus_08_FlowTemp_FlowTemp", config_entry=entry,
        device_id=dev.id, suggested_object_id="haustechnik_arotherm_plus_0_flowtemp")
    assert bare.entity_id == "sensor.haustechnik_arotherm_plus_0"

    with patch("custom_components.ebus.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)

    assert entry.minor_version == 7
    assert reg.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_08_StatusCirPump_StatusCirPump") \
        == "sensor.haustechnik_arotherm_plus_0_statuscirpump"
    assert reg.async_get(named.entity_id) is not None


async def test_migration_scopes_ids_to_the_entry(hass: HomeAssistant):
    # 1.4: every unique_id starts with the entry id (two adapters never collide);
    # the adapter's entities and device no longer depend on its IP address
    entry = _entry(minor_version=3)
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    adapter = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "1.2.3.4:9999")})
    reg = er.async_get(hass)
    sensor = reg.async_get_or_create("sensor", DOMAIN, "ebus_08_FlowTemp_FlowTemp",
                                     config_entry=entry)
    number = reg.async_get_or_create("number", DOMAIN,
                                     "ebus_15_HydraulicScheme_HydraulicScheme_set",
                                     config_entry=entry)
    signal = reg.async_get_or_create("binary_sensor", DOMAIN, "ebus_1.2.3.4_9999_signal",
                                     config_entry=entry)
    ident = reg.async_get_or_create("sensor", DOMAIN, "ebus_f6_artikelnummer",
                                    config_entry=entry)
    other = MockConfigEntry(domain="other")
    other.add_to_hass(hass)
    foreign = reg.async_get_or_create("sensor", "other", "ebus_08_x", config_entry=other)

    with patch("custom_components.ebus.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)

    e = entry.entry_id
    assert entry.minor_version == 7
    assert reg.async_get(sensor.entity_id).unique_id == f"{e}_08_FlowTemp_FlowTemp"
    assert reg.async_get(number.entity_id).unique_id == (
        f"{e}_15_HydraulicScheme_HydraulicScheme_set")
    assert reg.async_get(signal.entity_id).unique_id == f"{e}_adapter_signal"
    assert reg.async_get(ident.entity_id).unique_id == f"{e}_f6_artikelnummer"
    assert reg.async_get(foreign.entity_id).unique_id == "ebus_08_x"  # not ours
    assert devices.async_get(adapter.id).identifiers == {ADAPTER_IDENTIFIER}


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
