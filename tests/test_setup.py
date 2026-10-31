"""Setup, link supervision and registry tests (run under
pytest-homeassistant-custom-component; skipped where Home Assistant is not
installed)."""
import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from aioebus.engine import EbusEngine
from aioebus.passive import MessageRegistry
from aioebus.scan import ScanResult
from homeassistant.components.sensor import SensorStateClass
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ebus import climate
from custom_components.ebus.const import DOMAIN
from custom_components.ebus.coordinator import EbusCoordinator
from custom_components.ebus.library import all_messages, device_messages, fields_of
from custom_components.ebus.sensor import EbusSensor

_DATA = {"host": "1.2.3.4", "port": 9999}


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


def _entry(minor_version: int = 2) -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, data=_DATA, version=1, minor_version=minor_version)


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

    assert entry.minor_version == 2
    # the renamed counter keeps its entity_id (and so its history)
    assert reg.async_get(renamed).unique_id == "ebus_08_EnergyGeneratedHc_EnergyGeneratedHc"
    assert reg.async_get(retired) is None
    assert reg.async_get(orphan) is None  # its successor already existed
    assert reg.async_get(successor) is not None
    assert reg.async_get(kept) is not None


async def test_unreachable_adapter_retries_setup(hass: HomeAssistant):
    entry = _entry()
    entry.add_to_hass(hass)
    with patch("custom_components.ebus.coordinator.asyncio.open_connection",
               AsyncMock(side_effect=OSError("unreachable"))):
        await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


def _closed_link():
    reader = asyncio.StreamReader()
    reader.feed_eof()  # the adapter closed the connection
    writer = MagicMock()
    writer.drain = AsyncMock()
    return reader, writer


class _CountingEofReader:
    """A reader at EOF that counts reads; a busy loop trips the guard instead of
    hanging the test (the old loop never yielded, so no timeout could fire)."""

    def __init__(self):
        self.calls = 0

    async def read(self, _n: int) -> bytes:
        self.calls += 1
        if self.calls > 50:
            raise AssertionError("busy loop on EOF")
        return b""


async def test_link_closed_during_scan_retries_setup(hass: HomeAssistant):
    # an EOF must end the scan (not spin on read() returning b"") and retry setup
    entry = _entry()
    entry.add_to_hass(hass)
    reader = _CountingEofReader()
    _r, writer = _closed_link()
    with patch("custom_components.ebus.coordinator.asyncio.open_connection",
               AsyncMock(return_value=(reader, writer))):
        await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert reader.calls == 1


async def test_link_closed_during_identification_retries_setup(hass: HomeAssistant):
    entry = _entry()
    entry.add_to_hass(hass)
    hmu = ScanResult(0xB5, "Vaillant", "HMU00", "0902", "5103", 902, 5103)
    with patch("custom_components.ebus.coordinator.asyncio.open_connection",
               AsyncMock(return_value=_closed_link())), \
         patch("custom_components.ebus.coordinator.ActiveReader.scan",
               AsyncMock(return_value=hmu)), \
         patch("custom_components.ebus.coordinator.ActiveReader.read_identification",
               AsyncMock(side_effect=ConnectionResetError("closed"))):
        await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_recv_raises_on_eof(hass: HomeAssistant):
    entry = _entry()
    entry.add_to_hass(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    with patch("custom_components.ebus.coordinator.asyncio.open_connection",
               AsyncMock(return_value=_closed_link())):
        _send, recv = await coord._connect()
    with pytest.raises(ConnectionResetError):
        await recv()


async def test_recv_times_out_on_a_silent_link(hass: HomeAssistant):
    # half-open TCP: no EOF, just no bytes -- must not wait forever
    entry = _entry()
    entry.add_to_hass(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    writer = MagicMock()
    writer.drain = AsyncMock()
    with patch("custom_components.ebus.coordinator.asyncio.open_connection",
               AsyncMock(return_value=(asyncio.StreamReader(), writer))), \
         patch("custom_components.ebus.coordinator._RECV_TIMEOUT", 0.05):
        _send, recv = await coord._connect()
        with pytest.raises(TimeoutError):
            await recv()


async def test_supervisor_reconnects_after_link_loss(hass: HomeAssistant):
    entry = _entry()
    entry.add_to_hass(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    opener = AsyncMock(side_effect=lambda *a, **k: _closed_link())
    with patch("custom_components.ebus.coordinator.asyncio.open_connection", opener), \
         patch("custom_components.ebus.coordinator._RECONNECT_BACKOFF", 0):
        send, recv = await coord._connect()
        coord.engine = EbusEngine(send, recv, MessageRegistry([]))
        task = hass.async_create_task(coord._run_forever())
        async with asyncio.timeout(2):
            while opener.call_count < 3:  # dropped twice, reconnected each time
                await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_registry_listener_ignores_other_integrations(hass: HomeAssistant):
    entry = _entry()
    entry.add_to_hass(hass)
    other = MockConfigEntry(domain="other")
    other.add_to_hass(hass)
    reg = er.async_get(hass)
    ours = reg.async_get_or_create("sensor", DOMAIN, "ebus_08_X_X", config_entry=entry)
    theirs = reg.async_get_or_create("sensor", "other", "x", config_entry=other)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    with patch.object(coord._rescope, "async_schedule_call") as schedule:
        for entity_id, action, changes in (
            (theirs.entity_id, "create", None),
            (theirs.entity_id, "update", {"disabled_by": None}),
            (ours.entity_id, "update", {"name": None}),  # not an enable/disable
        ):
            data = {"action": action, "entity_id": entity_id}
            if changes is not None:
                data["changes"] = changes
            coord._on_registry_update(Event(er.EVENT_ENTITY_REGISTRY_UPDATED, data))
        schedule.assert_not_called()
        coord._on_registry_update(Event(er.EVENT_ENTITY_REGISTRY_UPDATED,
                                        {"action": "create", "entity_id": ours.entity_id}))
        coord._on_registry_update(Event(er.EVENT_ENTITY_REGISTRY_UPDATED, {
            "action": "update", "entity_id": ours.entity_id,
            "changes": {"disabled_by": None}}))
        assert schedule.call_count == 2


async def test_past_period_energy_has_no_state_class(hass: HomeAssistant):
    entry = _entry()
    entry.add_to_hass(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    coord.devices = {0x15: ScanResult(0xB5, "Vaillant", "CTLV3", "0808", "8004", 808, 8004)}
    coord.data = {}
    sensors = {f.msg.name: EbusSensor(coord, f)
               for m in device_messages(coord.devices, "r") for f in fields_of(m)}
    assert sensors["PrEnergySumHcLastMonth"].state_class is None
    hmu = {0x08: ScanResult(0xB5, "Vaillant", "HMU00", "0902", "5103", 902, 5103)}
    coord.devices = hmu
    gen = next(f for m in device_messages(hmu, "r") for f in fields_of(m)
               if m.name == "EnergyGeneratedHc")
    assert EbusSensor(coord, gen).state_class is SensorStateClass.TOTAL_INCREASING


async def test_warm_start_drops_values_of_unknown_registers(hass: HomeAssistant):
    entry = _entry()
    entry.add_to_hass(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    msgs = all_messages({0x08: ScanResult(0xB5, "Vaillant", "HMU00", "0902", "5103", 902, 5103)})
    coord.engine = EbusEngine(AsyncMock(), AsyncMock(), MessageRegistry(msgs))
    stored = {"values": [[["08", "FlowTemp", "FlowTemp"], 40.5],
                         [["08", "SupplyTempWeighted", "SupplyTempWeighted"], 31.0],
                         [["18", "FlowTemp", "FlowTemp"], 38.0]]}
    with patch.object(coord._store, "async_load", AsyncMock(return_value=stored)):
        await coord._warm_start_cache()
    # the retired register of the scanned device goes; the device at 18 merely
    # missed this scan and keeps its values for the next start
    assert coord.engine.cache == {("08", "FlowTemp", "FlowTemp"): 40.5,
                                  ("18", "FlowTemp", "FlowTemp"): 38.0}


async def test_climate_zone_added_once_its_data_arrives(hass: HomeAssistant):
    # a fresh install has no cached values: the zone must still appear later
    entry = _entry()
    entry.add_to_hass(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    coord.update_interval = None  # no refresh timer in this test
    coord.devices = {0x15: ScanResult(0xB5, "Vaillant", "CTLV3", "0808", "8004", 808, 8004)}
    coord.data = {}
    entry.runtime_data = coord
    added: list = []
    await climate.async_setup_entry(hass, entry, lambda ents: added.extend(ents))
    assert added == []
    coord.async_set_updated_data({("15", "Z1RoomTemp", "Z1RoomTemp"): 21.5})
    assert [e.unique_id for e in added] == ["ebus_15_z1_climate"]
    coord.async_set_updated_data({("15", "Z1RoomTemp", "Z1RoomTemp"): 21.0})
    assert len(added) == 1  # added once, not on every update
