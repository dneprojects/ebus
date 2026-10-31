"""Round-3 behaviour: remembered identification, kept extra addresses, one-shot
deep scan, stored-file clean-up, translation fetch, link-loss logging (run under
pytest-homeassistant-custom-component; skipped where Home Assistant is not
installed)."""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from aioebus.active import ReadError
from aioebus.scan import Identification, ScanResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ebus import _remove_orphaned_stores, async_remove_entry, translate
from custom_components.ebus.const import CONF_DEEP_SCAN, DOMAIN
from custom_components.ebus.coordinator import EbusCoordinator

_HMU = ScanResult(0xB5, "Vaillant", "HMU00", "0902", "5103", 902, 5103)
_GW = ScanResult(0xB5, "Vaillant", "NETX3", "0130", "0404", 130, 404)


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


def _coord(hass: HomeAssistant, **options) -> EbusCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            options=options, version=1, minor_version=4)
    entry.add_to_hass(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    coord.update_interval = None
    return coord


async def test_identification_is_remembered(hass: HomeAssistant):
    # the gateway answers once, then not (busy after a restart): the remembered
    # identification stands in, so article/serial stay available
    coord = _coord(hass)
    coord.devices = {0xF6: _GW}
    ident = Identification("0020260914", "21234500100260914000000000N1", 2021, 23)
    scanner = MagicMock()
    scanner.read_identification = AsyncMock(return_value=ident)
    await coord._identify(scanner)
    assert coord.idents[0xF6] == ident

    coord.idents = {}
    scanner.read_identification = AsyncMock(side_effect=ReadError("no answer from slave"))
    await coord._identify(scanner)
    assert coord.idents[0xF6] == ident

    # a different device now at that address does not inherit it
    coord.idents = {}
    coord.devices = {0xF6: _HMU}
    scanner.read_identification = AsyncMock(return_value=None)
    await coord._identify(scanner)
    assert 0xF6 not in coord.idents


async def test_normal_scan_keeps_extras_that_miss_one_start(hass: HomeAssistant):
    coord = _coord(hass)
    await coord._addr_store.async_save([0x76, 0x52])  # 0x52 found by an earlier deep scan
    present = {0x08: _HMU, 0x76: _HMU}  # 0x52 misses this scan

    async def scan(zz, timeout):
        if zz in present:
            return present[zz]
        raise ReadError("no answer from slave")

    scanner = MagicMock()
    scanner.scan = AsyncMock(side_effect=scan)
    scanner.read_identification = AsyncMock(return_value=None)
    with patch.object(coord, "_connect", AsyncMock(return_value=(AsyncMock(), AsyncMock()))), \
         patch("custom_components.ebus.coordinator.ActiveReader", return_value=scanner), \
         patch.object(coord, "_fetch_adapter_status", AsyncMock(return_value={})), \
         patch("custom_components.ebus.coordinator.async_update_translations", AsyncMock()), \
         patch.object(coord, "_run_forever", AsyncMock()):
        await coord._discover()
    assert sorted(await coord._addr_store.async_load()) == [0x52, 0x76]  # 0x52 not forgotten
    adapter = dr.async_get(hass).async_get(coord.adapter_device_id)
    assert adapter is not None and adapter.config_entry_id == coord.config_entry.entry_id
    await coord.async_shutdown()


async def test_full_setup_uses_current_registry_apis(hass: HomeAssistant, caplog):
    # the whole setup with platforms: entry-scoped ids, devices linked to the
    # adapter by via_device_id, and no deprecated registry call or blocking I/O
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            version=1, minor_version=4)
    entry.add_to_hass(hass)
    idle = asyncio.Event()

    async def recv() -> bytes:
        await idle.wait()  # a bus that stays silent for the test
        return b""

    scanner = MagicMock()
    scanner.scan = AsyncMock(side_effect=lambda zz, timeout: _HMU if zz == 0x08
                             else (_ for _ in ()).throw(ReadError("no answer")))
    scanner.read_identification = AsyncMock(return_value=None)
    deprecated = MagicMock(side_effect=AssertionError("deprecated async_get_device"))
    with patch.object(dr.DeviceRegistry, "async_get_device", deprecated), \
         patch("custom_components.ebus.coordinator.EbusCoordinator._connect",
               AsyncMock(return_value=(AsyncMock(), recv))), \
         patch("custom_components.ebus.coordinator.ActiveReader", return_value=scanner), \
         patch("custom_components.ebus.coordinator.EbusCoordinator._fetch_adapter_status",
               AsyncMock(return_value={})), \
         patch("custom_components.ebus.coordinator.async_update_translations", AsyncMock()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    devices = dr.async_get(hass)
    coord = entry.runtime_data
    hmu = devices.async_get_device_by_identifier((DOMAIN, "08"), entry.entry_id)
    assert hmu is not None and hmu.via_device_id == coord.adapter_device_id
    assert "via_device" not in coord.device_info(0x08)  # the deprecated key
    assert coord.device_info(0x08)["via_device_id"] == coord.adapter_device_id
    deprecated.assert_not_called()
    from homeassistant.helpers import entity_registry as er
    ents = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert ents and all(e.unique_id.startswith(f"{entry.entry_id}_") for e in ents)
    assert not [r for r in caplog.records
                if "Detected" in r.getMessage() or "blocking" in r.getMessage()]
    idle.set()
    assert await hass.config_entries.async_unload(entry.entry_id)


def test_orphaned_stores_are_removed(tmp_path):
    for name in ("ebus_cache_KEEP", "ebus_addrs_KEEP", "ebus_cache_GONE", "ebus_idents_GONE",
                 "ebus_cache_KEEP.corrupt.2026-10-31T10:00:00", "ebus_bridge_settings",
                 "core.config_entries"):
        (tmp_path / name).write_text("{}")
    removed = _remove_orphaned_stores(str(tmp_path), {"KEEP"})
    assert sorted(removed) == ["ebus_cache_GONE", "ebus_idents_GONE"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "core.config_entries", "ebus_addrs_KEEP", "ebus_bridge_settings", "ebus_cache_KEEP",
        "ebus_cache_KEEP.corrupt.2026-10-31T10:00:00"]  # HA's copy for a repair issue


async def test_removing_the_entry_deletes_its_stores(hass: HomeAssistant, hass_storage):
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999})
    for prefix in ("ebus_cache_", "ebus_addrs_", "ebus_idents_"):
        hass_storage[f"{prefix}{entry.entry_id}"] = {"version": 1, "data": {}}
    await async_remove_entry(hass, entry)
    assert not [k for k in hass_storage if k.endswith(entry.entry_id)]


def test_link_loss_is_logged_once_per_outage(hass: HomeAssistant, caplog):
    coord = _coord(hass)
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            coord._log_once("eBUS: link to %s:%s lost, reconnecting", "1.2.3.4", 9999)
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


_CSV = ("type,circuit,level,name,comment,qq,zz,pbsb,id,*name,part,type,divisor/values,unit,comment\n"
        "r,,,FlowTemp,flow temperature,,,b509,0d1800,value,,D2C,,°C,\n")


@pytest.fixture
def tdir(tmp_path, monkeypatch):
    monkeypatch.setattr(translate, "_TDIR", tmp_path)
    monkeypatch.setattr(translate, "_MARKER", tmp_path / ".ebus_gen")
    return tmp_path


async def test_partial_cdn_fetch_is_not_remembered(hass: HomeAssistant, tdir):
    hass.config.language = "de"
    calls = []

    async def fetch(_session, lang, path):
        calls.append(lang)
        return None if lang == "de" else _CSV  # the German file is unreachable this time

    with patch.object(translate, "_fetch", fetch), \
         patch.object(translate, "async_get_integration",
                      AsyncMock(return_value=MagicMock(version="0.5.0"))):
        await translate.async_update_translations(hass, {0x08: _HMU}, None)
        first = len(calls)
        await translate.async_update_translations(hass, {0x08: _HMU}, None)
    assert (tdir / "en.json").exists()
    assert len(calls) == 2 * first  # fetched again: the gap is not kept


async def test_language_the_cdn_lacks_is_not_refetched(hass: HomeAssistant, tdir):
    # upstream has no French files (404 -> ""): that is final, not a gap to retry
    hass.config.language = "fr"
    calls = []

    async def fetch(_session, lang, path):
        calls.append(lang)
        return "" if lang == "fr" else _CSV

    with patch.object(translate, "_fetch", fetch), \
         patch.object(translate, "async_get_integration",
                      AsyncMock(return_value=MagicMock(version="0.5.0"))):
        await translate.async_update_translations(hass, {0x08: _HMU}, None)
        first = len(calls)
        await translate.async_update_translations(hass, {0x08: _HMU}, None)
    assert first and len(calls) == first  # remembered: no fetch on the second start


async def test_user_files_are_reread_on_setup(tmp_path):
    from custom_components.ebus import library
    dev = {0x26: ScanResult(0xB5, "Vaillant", "VR71X", "0000", "0000", 0, 0)}
    library.preload(dev, tmp_path)
    assert library.all_messages(dev, tmp_path) == []
    (tmp_path / "26.vr71x.json").write_text(
        '[{"direction": "r", "name": "Foo", "pbsb": "b509", "id": "0d0100",'
        ' "fields": [{"name": "value", "type": "UCH"}]}]', encoding="utf-8")
    library.preload(dev, tmp_path)  # the next setup (e.g. after a reload)
    assert [m.name for m in library.all_messages(dev, tmp_path)] == ["Foo"]


async def test_deep_scan_drops_addresses_nothing_answers_on(hass: HomeAssistant):
    coord = _coord(hass, **{CONF_DEEP_SCAN: True})
    await coord._addr_store.async_save([0x52, 0x53])  # stale: no device there any more
    scanner = MagicMock()
    scanner.scan = AsyncMock(side_effect=lambda zz, timeout: _HMU if zz == 0x08
                             else (_ for _ in ()).throw(ReadError("no answer")))
    scanner.read_identification = AsyncMock(return_value=None)
    with patch.object(coord, "_connect", AsyncMock(return_value=(AsyncMock(), AsyncMock()))), \
         patch("custom_components.ebus.coordinator.ActiveReader", return_value=scanner), \
         patch.object(coord, "_fetch_adapter_status", AsyncMock(return_value={})), \
         patch("custom_components.ebus.coordinator.async_update_translations", AsyncMock()), \
         patch.object(coord, "_run_forever", AsyncMock()):
        await coord._discover()
    assert await coord._addr_store.async_load() == []
    assert coord.config_entry.options[CONF_DEEP_SCAN] is False  # done: probe once only
    await coord.async_shutdown()


async def test_remembered_identification_needs_the_same_unit(hass: HomeAssistant):
    # a replaced unit of the same model (other hw/sw) must not inherit the serial
    coord = _coord(hass)
    coord.devices = {0xF6: _GW}
    ident = Identification("0020260914", "21234500100260914000000000N1", 2021, 23)
    scanner = MagicMock()
    scanner.read_identification = AsyncMock(return_value=ident)
    await coord._identify(scanner)
    coord.idents = {}
    coord.devices = {0xF6: ScanResult(0xB5, "Vaillant", "NETX3", "0200", "0404", 200, 404)}
    scanner.read_identification = AsyncMock(return_value=None)
    await coord._identify(scanner)
    assert 0xF6 not in coord.idents


async def test_link_restored_is_logged_and_rearms(hass: HomeAssistant, caplog):
    coord = _coord(hass)
    coord.engine = MagicMock()
    coord.engine.cache = {}
    coord.engine.stats.connected = True
    coord._log_once("eBUS: link to %s:%s lost, reconnecting", "1.2.3.4", 9999)
    with caplog.at_level(logging.INFO):
        await coord._async_update_data()
    assert "restored" in caplog.text and coord._link_lost_logged is False


async def test_slow_cdn_does_not_hold_up_setup(hass: HomeAssistant, tdir, monkeypatch):
    monkeypatch.setattr(translate, "_FETCH_BUDGET", 0.05)

    async def fetch(_session, _lang, _path):
        await asyncio.sleep(5)

    with patch.object(translate, "_fetch", fetch), \
         patch.object(translate, "async_get_integration",
                      AsyncMock(return_value=MagicMock(version="0.5.0"))):
        async with asyncio.timeout(1):
            await translate.async_update_translations(hass, {0x08: _HMU}, None)
    assert not (tdir / "en.json").exists()


async def test_cache_clear_only_touches_this_integration(hass: HomeAssistant):
    from homeassistant.helpers.translation import _async_get_translations_cache
    cache = _async_get_translations_cache(hass)
    cache.cache_data.loaded["en"] = {DOMAIN, "other"}
    cache.cache_data.cache["en"] = {"entity": {DOMAIN: {"a": "1"}, "other": {"b": "2"}}}
    translate._clear_cache(hass)
    assert cache.cache_data.loaded["en"] == {"other"}
    assert cache.cache_data.cache["en"]["entity"] == {"other": {"b": "2"}}
