"""Setup behaviour around discovery (stored addresses and identifications, the
one-shot deep scan, late identification), stored-file clean-up, translation
fetch, link-loss logging (run under pytest-homeassistant-custom-component;
skipped where Home Assistant is not installed). The discovery itself (scan,
identification, duplicates) is aioebus's and tested there."""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from aioebus.active import ReadError
from aioebus.catalog import Catalog
from aioebus.discovery import Device, Discovery
from aioebus.scan import Identification, ScanResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ebus import _remove_orphaned_stores, async_remove_entry, translate
from custom_components.ebus.const import CONF_DEEP_SCAN, DOMAIN
from custom_components.ebus.coordinator import EbusCoordinator

_HMU = ScanResult(0xB5, "Vaillant", "HMU00", "0902", "5103", 902, 5103)
_GW = ScanResult(0xB5, "Vaillant", "NETX3", "0130", "0404", 130, 404)
_IDENT = Identification("0010021122", "21212300100210211220000000N7", 2021, 12)


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


async def _idle() -> bytes:
    """recv of a link that stays quiet (the discovery itself is patched)."""
    await asyncio.Event().wait()
    return b""


def _found(devices, extra=(), remembered=None, dropped=()):
    return Discovery({zz: Device(zz, scan, ident) for zz, (scan, ident) in devices.items()},
                     list(extra), dict(remembered or {}), set(dropped))


async def _catalog(devices) -> Catalog:
    catalog = Catalog()  # bundled definitions only
    await catalog.load(devices)
    return catalog


def _coord(hass: HomeAssistant, **options) -> EbusCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            options=options, version=1, minor_version=7)
    entry.add_to_hass(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    coord.update_interval = None
    return coord


async def _discover_with(coord: EbusCoordinator, found: Discovery) -> AsyncMock:
    discover = AsyncMock(return_value=found)
    with patch.object(coord, "_connect", AsyncMock(return_value=(AsyncMock(), _idle))), \
         patch("custom_components.ebus.coordinator.discover", discover), \
         patch("custom_components.ebus.coordinator.fetch_status", AsyncMock(return_value={})), \
         patch("custom_components.ebus.coordinator.async_update_translations", AsyncMock()), \
         patch.object(coord, "_run_forever", AsyncMock()), \
         patch.object(coord, "_late_identify", AsyncMock()):
        await coord._discover()
    return discover


async def test_discovery_results_are_kept_for_the_next_start(hass: HomeAssistant):
    coord = _coord(hass)
    await coord._addr_store.async_save([0x76, 0x52])
    await coord._ident_store.async_save({"f6": {"serial": "old"}})
    record = {"ident": "HMU00", "hw": "5103", "sw": "0902", "serial": _IDENT.serial}
    discover = await _discover_with(coord, _found(
        {0x08: (_HMU, _IDENT), 0x76: (_HMU, None)}, extra=[0x52, 0x76],
        remembered={"f6": {"serial": "old"}, "08": record}))
    kwargs = discover.await_args.kwargs
    assert kwargs["extra_addresses"] == [0x76, 0x52] and kwargs["deep"] is False
    assert kwargs["remembered"] == {"f6": {"serial": "old"}}
    assert sorted(await coord._addr_store.async_load()) == [0x52, 0x76]
    assert (await coord._ident_store.async_load())["08"] == record
    assert coord.idents == {0x08: _IDENT} and set(coord.devices) == {0x08, 0x76, 0xFE}
    adapter = dr.async_get(hass).async_get(coord.adapter_device_id)
    assert adapter is not None and adapter.config_entry_id == coord.config_entry.entry_id
    await coord.async_shutdown()


async def test_deep_scan_runs_once(hass: HomeAssistant):
    coord = _coord(hass, **{CONF_DEEP_SCAN: True})
    await coord._addr_store.async_save([0x52, 0x53])  # stale: nothing there any more
    discover = await _discover_with(coord, _found({0x08: (_HMU, None)}))
    assert discover.await_args.kwargs["deep"] is True
    assert await coord._addr_store.async_load() == []
    assert coord.config_entry.options[CONF_DEEP_SCAN] is False  # done: probe once only
    await coord.async_shutdown()


async def test_duplicate_address_leaves_the_device_registry(hass: HomeAssistant):
    coord = _coord(hass)
    devices = dr.async_get(hass)
    devices.async_get_or_create(config_entry_id=coord.config_entry.entry_id,
                                identifiers={(DOMAIN, "52")})
    await _discover_with(coord, _found({0x18: (_HMU, _IDENT)}, dropped={0x52}))
    assert devices.async_get_device_by_identifier(
        (DOMAIN, "52"), coord.config_entry.entry_id) is None
    await coord.async_shutdown()


async def test_full_setup_uses_current_registry_apis(hass: HomeAssistant, caplog):
    # the whole setup with platforms: entry-scoped ids, devices linked to the
    # adapter by via_device_id, and no deprecated registry call or blocking I/O
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            version=1, minor_version=4)
    entry.add_to_hass(hass)
    deprecated = MagicMock(side_effect=AssertionError("deprecated async_get_device"))
    with patch.object(dr.DeviceRegistry, "async_get_device", deprecated), \
         patch("custom_components.ebus.coordinator.EbusCoordinator._connect",
               AsyncMock(return_value=(AsyncMock(), _idle))), \
         patch("custom_components.ebus.coordinator.discover",
               AsyncMock(return_value=_found({0x08: (_HMU, None)}))), \
         patch("custom_components.ebus.coordinator.fetch_status",
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
        await translate.async_update_translations(hass, "e1", await _catalog({0x08: _HMU}))
        first = len(calls)
        await translate.async_update_translations(hass, "e1", await _catalog({0x08: _HMU}))
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
        await translate.async_update_translations(hass, "e1", await _catalog({0x08: _HMU}))
        first = len(calls)
        await translate.async_update_translations(hass, "e1", await _catalog({0x08: _HMU}))
    assert first and len(calls) == first  # remembered: no fetch on the second start


async def test_identification_missed_at_setup_is_read_at_runtime(hass: HomeAssistant):
    # a heat pump behind the coupler skipped the identification at setup: the
    # running engine reads it later, stores it and tells the platforms
    coord = _coord(hass)
    coord._found = {0x18: Device(0x18, _HMU)}
    coord.engine = MagicMock()
    coord.engine.read_identification = AsyncMock(side_effect=[None, _IDENT])
    listener = MagicMock()
    coord.async_add_listener(listener)
    with patch("custom_components.ebus.coordinator._IDENT_RETRY_DELAYS", (0, 0, 0)):
        await coord._late_identify()
    assert coord.idents[0x18] == _IDENT
    assert (await coord._ident_store.async_load())["18"]["serial"] == _IDENT.serial
    listener.assert_called()


@pytest.mark.parametrize("answer", [TimeoutError(), OSError("reset"), ConnectionError(),
                                    ReadError("NAK"), None])
async def test_identification_that_never_comes_gives_up_quietly(hass: HomeAssistant, answer):
    # every retry fails (or the answer is not a Vaillant identification): no
    # ident, nothing stored, no exception out of the background task
    coord = _coord(hass)
    coord._found = {0x18: Device(0x18, _HMU)}
    coord.engine = MagicMock()
    coord.engine.read_identification = (
        AsyncMock(return_value=None) if answer is None else AsyncMock(side_effect=answer))
    listener = MagicMock()
    coord.async_add_listener(listener)
    with patch("custom_components.ebus.coordinator._IDENT_RETRY_DELAYS", (0, 0, 0)):
        await coord._late_identify()
    assert coord.engine.read_identification.await_count == 3
    assert 0x18 not in coord.idents
    assert await coord._ident_store.async_load() is None
    listener.assert_not_called()


async def test_identification_waits_for_the_engine(hass: HomeAssistant):
    coord = _coord(hass)
    coord._found = {0x18: Device(0x18, _HMU)}
    coord.engine = None
    with patch("custom_components.ebus.coordinator._IDENT_RETRY_DELAYS", (0, 0)):
        await coord._late_identify()
    assert 0x18 not in coord.idents


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
            await translate.async_update_translations(hass, "e1", await _catalog({0x08: _HMU}))
    # names from the definitions meanwhile, without a signature: the next start
    # fetches the CDN again
    assert (tdir / "en.json").exists()
    assert (tdir / ".ebus_gen").read_text(encoding="utf-8").startswith("\n")


async def test_cache_clear_only_touches_this_integration(hass: HomeAssistant):
    from homeassistant.helpers.translation import _async_get_translations_cache
    cache = _async_get_translations_cache(hass)
    cache.cache_data.loaded["en"] = {DOMAIN, "other"}
    cache.cache_data.cache["en"] = {"entity": {DOMAIN: {"a": "1"}, "other": {"b": "2"}}}
    translate._clear_cache(hass)
    assert cache.cache_data.loaded["en"] == {"other"}
    assert cache.cache_data.cache["en"]["entity"] == {"other": {"b": "2"}}
