"""Quick setup from the devices found last time, with the bus scanned in the
background (run under pytest-homeassistant-custom-component)."""
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from aioebus.discovery import Device, Discovery
from aioebus.scan import ScanResult
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ebus import coordinator as coord_mod
from custom_components.ebus.const import DOMAIN
from custom_components.ebus.coordinator import EbusCoordinator, _devices_from, _scan_record

HMU = ScanResult(0xB5, "Vaillant", "HMU00", "0902", "5103", 902, 5103)
CTL = ScanResult(0xB5, "Vaillant", "CTLV3", "0808", "8004", 808, 8004)
VR71 = ScanResult(0xB5, "Vaillant", "VR_71", "0203", "5904", 203, 5904)


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


def _entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            version=1, minor_version=2)
    entry.add_to_hass(hass)
    return entry


def test_stored_devices_are_read_back_and_bad_records_skipped():
    stored = {"08": _scan_record(HMU), "15": _scan_record(CTL),
              "zz": _scan_record(HMU), "26": {"ident": "VR_71"}}
    devices = _devices_from(stored, {})
    assert {zz: d.scan for zz, d in devices.items()} == {0x08: HMU, 0x15: CTL}
    assert _devices_from(None, {}) == {} and _devices_from(["x"], {}) == {}


async def _coordinator(hass, devices):
    entry = _entry(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    coord.devices = {zz: d.scan for zz, d in devices.items()}
    coord._found = dict(devices)
    coord.engine = AsyncMock()
    return entry, coord


async def test_a_quick_setup_does_not_scan_and_checks_in_the_background(hass: HomeAssistant):
    entry = _entry(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    await coord._device_store.async_save({"08": _scan_record(HMU), "15": _scan_record(CTL)})
    discover = AsyncMock()
    with patch.object(coord_mod, "discover", discover), \
         patch.object(coord_mod.EngineRunner, "start", AsyncMock()), \
         patch.object(EbusCoordinator, "_finish_setup", AsyncMock()) as finish, \
         patch.object(EbusCoordinator, "_verify_devices", AsyncMock()) as verify:
        await coord._discover()
        await hass.async_block_till_done()
    discover.assert_not_called()  # the setup did not wait for the bus
    assert coord.devices == {0x08: HMU, 0x15: CTL}
    finish.assert_awaited_once_with(translate_inline=False)
    verify.assert_awaited_once()


async def test_a_new_device_found_in_the_background_reloads(hass: HomeAssistant):
    entry, coord = await _coordinator(hass, {0x08: Device(0x08, HMU)})
    found = Discovery({0x08: Device(0x08, HMU), 0x26: Device(0x26, VR71)}, [], {})
    with patch.object(coord_mod, "_VERIFY_DELAYS", (0,)), \
         patch.object(coord_mod, "discover", AsyncMock(return_value=found)), \
         patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await coord._verify_devices([], {})
    reload.assert_called_once_with(entry.entry_id)
    stored = await coord._device_store.async_load()
    assert set(stored) == {"08", "26"}


async def test_a_known_device_missing_in_the_background_stays(hass: HomeAssistant):
    # a busy bus: no reload that would drop it, it is looked for again
    _entry_, coord = await _coordinator(
        hass, {0x08: Device(0x08, HMU), 0x26: Device(0x26, VR71)})
    found = Discovery({0x08: Device(0x08, HMU)}, [], {})
    with patch.object(coord_mod, "_VERIFY_DELAYS", (0,)), \
         patch.object(coord_mod, "discover", AsyncMock(return_value=found)), \
         patch.object(hass.config_entries, "async_schedule_reload") as reload, \
         patch.object(EbusCoordinator, "_late_find", AsyncMock()) as late:
        await coord._verify_devices([], {})
        await hass.async_block_till_done()
    reload.assert_not_called()
    late.assert_awaited_once_with([0x26])
    stored = await coord._device_store.async_load()
    assert set(stored) == {"08", "26"}  # still known for the next start


async def test_the_same_devices_change_nothing(hass: HomeAssistant):
    _entry_, coord = await _coordinator(hass, {0x08: Device(0x08, HMU)})
    found = Discovery({0x08: Device(0x08, HMU)}, [], {})
    with patch.object(coord_mod, "_VERIFY_DELAYS", (0,)), \
         patch.object(coord_mod, "discover", AsyncMock(return_value=found)), \
         patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await coord._verify_devices([], {})
    reload.assert_not_called()


async def test_a_first_setup_scans_and_stores_the_devices(hass: HomeAssistant):
    entry = _entry(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    found = Discovery({0x08: Device(0x08, HMU), 0x15: Device(0x15, CTL)}, [], {})
    with patch.object(coord_mod, "discover", AsyncMock(return_value=found)) as discover, \
         patch.object(coord_mod.EngineRunner, "start", AsyncMock()), \
         patch.object(EbusCoordinator, "_finish_setup", AsyncMock()) as finish:
        await coord._discover()
    discover.assert_awaited_once()
    finish.assert_awaited_once_with(translate_inline=True)
    assert set(await coord._device_store.async_load()) == {"08", "15"}


async def test_a_known_gateway_is_not_scanned_again(hass: HomeAssistant):
    netx = ScanResult(0xB5, "Vaillant", "NETX3", "0128", "1803", 128, 1803)
    _entry_, coord = await _coordinator(
        hass, {0x08: Device(0x08, HMU), 0xF6: Device(0xF6, netx)})
    found = Discovery({0x08: Device(0x08, HMU), 0xF6: Device(0xF6, netx)}, [], {})
    with patch.object(coord_mod, "_VERIFY_DELAYS", (0,)), \
         patch.object(coord_mod, "discover", AsyncMock(return_value=found)) as discover, \
         patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await coord._verify_devices([], {})
    assert set(discover.await_args.kwargs["untouched"]) == {0xF6}
    reload.assert_not_called()
