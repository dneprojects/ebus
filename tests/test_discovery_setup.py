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


def _url_fetch(fetch):
    """A catalog fetch from a (session, lang, path) stand-in of the CDN."""
    if fetch is None:
        return None

    async def by_url(url):
        lang, path = url.removeprefix("https://ebus.github.io/").split("/", 1)
        return await fetch(None, lang, path.removesuffix(".csv"))

    return by_url


async def _catalog(devices, fetch=None) -> Catalog:
    catalog = Catalog(fetch=_url_fetch(fetch))  # bundled definitions, texts via fetch
    await catalog.load(devices)
    return catalog


def _coord(hass: HomeAssistant, **options) -> EbusCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            options=options, version=1, minor_version=7)
    entry.add_to_hass(hass)
    coord = EbusCoordinator(hass, entry, "1.2.3.4", 9999)
    coord.update_interval = None
    return coord


def _idle_link() -> MagicMock:
    """An adapter link that stays quiet (the discovery itself is patched)."""
    link = MagicMock()
    link.send, link.recv = AsyncMock(), _idle
    return link


async def _discover_with(coord: EbusCoordinator, found: Discovery) -> AsyncMock:
    discover = AsyncMock(return_value=found)
    with patch("custom_components.ebus.coordinator.connect",
               AsyncMock(return_value=_idle_link())), \
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
    assert coord.idents == {0x08: _IDENT} and set(coord.devices) == {0x08, 0x76}
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
         patch("custom_components.ebus.coordinator.connect",
               AsyncMock(return_value=_idle_link())), \
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

    with patch.object(translate, "async_get_integration",
                      AsyncMock(return_value=MagicMock(version="0.5.0"))):
        await translate.async_update_translations(hass, "e1", await _catalog({0x08: _HMU}, fetch=fetch))
        first = len(calls)
        await translate.async_update_translations(hass, "e1", await _catalog({0x08: _HMU}, fetch=fetch))
    assert (tdir / "en.json").exists()
    assert len(calls) == 2 * first  # fetched again: the gap is not kept


async def test_language_the_cdn_lacks_is_not_refetched(hass: HomeAssistant, tdir):
    # upstream has no French files (404 -> ""): that is final, not a gap to retry
    hass.config.language = "fr"
    calls = []

    async def fetch(_session, lang, path):
        calls.append(lang)
        return "" if lang == "fr" else _CSV

    with patch.object(translate, "async_get_integration",
                      AsyncMock(return_value=MagicMock(version="0.5.0"))):
        await translate.async_update_translations(hass, "e1", await _catalog({0x08: _HMU}, fetch=fetch))
        first = len(calls)
        await translate.async_update_translations(hass, "e1", await _catalog({0x08: _HMU}, fetch=fetch))
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

    with patch.object(translate, "async_get_integration",
                      AsyncMock(return_value=MagicMock(version="0.5.0"))):
        async with asyncio.timeout(1):
            await translate.async_update_translations(hass, "e1", await _catalog({0x08: _HMU}, fetch=fetch))
    # names from the definitions meanwhile, without a signature: the next start
    # fetches the CDN again
    assert (tdir / "en.json").exists()
    assert (tdir / ".ebus_gen").read_text(encoding="utf-8").startswith("{}\n")


async def test_cache_clear_only_touches_this_integration(hass: HomeAssistant):
    from homeassistant.helpers.translation import _async_get_translations_cache
    cache = _async_get_translations_cache(hass)
    cache.cache_data.loaded["en"] = {DOMAIN, "other"}
    cache.cache_data.cache["en"] = {"entity": {DOMAIN: {"a": "1"}, "other": {"b": "2"}}}
    translate._clear_cache(hass)
    assert cache.cache_data.loaded["en"] == {"other"}
    assert cache.cache_data.cache["en"]["entity"] == {"other": {"b": "2"}}


def _setup_patches(link, found, status):
    return (patch("custom_components.ebus.coordinator.connect", AsyncMock(return_value=link)),
            patch("custom_components.ebus.coordinator.discover", AsyncMock(return_value=found)),
            patch("custom_components.ebus.coordinator.fetch_status", status),
            patch("custom_components.ebus.coordinator.async_update_translations", AsyncMock()))


async def test_setup_retry_keeps_the_saved_values(hass: HomeAssistant, hass_storage):
    # a silent bus (heating off) sends the setup into retry: the values saved at
    # the last shutdown must survive it for the warm start once the bus is back
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            version=1, minor_version=7)
    entry.add_to_hass(hass)
    saved = {"version": 1, "key": f"ebus_cache_{entry.entry_id}",
             "data": {"values": [[["08", "FlowTemp", "FlowTemp"], 40.5]]}}
    hass_storage[f"ebus_cache_{entry.entry_id}"] = dict(saved)
    from aioebus.discovery import BusSilent
    with patch("custom_components.ebus.coordinator.connect",
               AsyncMock(return_value=_idle_link())), \
         patch("custom_components.ebus.coordinator.discover",
               AsyncMock(side_effect=BusSilent("no eBUS traffic"))):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    from homeassistant.config_entries import ConfigEntryState
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert hass_storage[f"ebus_cache_{entry.entry_id}"]["data"] == saved["data"]


async def test_failed_platform_setup_closes_the_link(hass: HomeAssistant):
    # discovery done, then the setup fails: the link and the engine must not
    # outlive the failed entry
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            version=1, minor_version=7)
    entry.add_to_hass(hass)
    link = _idle_link()
    p1, p2, p3, p4 = _setup_patches(link, _found({0x08: (_HMU, None)}),
                                    AsyncMock(return_value={}))
    with p1, p2, p3, p4, patch.object(hass.config_entries, "async_forward_entry_setups",
                                      AsyncMock(side_effect=RuntimeError("platform broke"))):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    link.close.assert_called()
    assert not [t for t in asyncio.all_tasks()
                if t.get_name() in ("ebus-link", "ebus-supervisor") and not t.done()]


async def test_web_status_answering_late_adds_its_sensors(hass: HomeAssistant):
    # the adapter's web API was slow at setup: it is asked again later, then its
    # sensors appear and the adapter device gets its firmware
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            version=1, minor_version=7)
    entry.add_to_hass(hass)
    status = {"id": "58e6c5e3ba58", "versions": "fw 1.2", "chip": "ESP32-C3",
              "sta": {"rssi": -60}, "uptime": 7200, "heap": 2_000_000}
    fetch = AsyncMock(return_value={})
    p1, p2, p3, p4 = _setup_patches(_idle_link(), _found({0x08: (_HMU, None)}), fetch)
    with p1, p2, p3, p4:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coord = entry.runtime_data
        from homeassistant.helpers import entity_registry as er
        reg = er.async_get(hass)
        assert reg.async_get_entity_id("sensor", DOMAIN, coord.uid("adapter", "wlan_signal")) is None
        fetch.return_value = status
        coord._web_asked -= 3600  # the retry is due
        await coord.async_refresh()
        await hass.async_block_till_done()
        assert reg.async_get_entity_id("sensor", DOMAIN, coord.uid("adapter", "wlan_signal"))
        adapter = dr.async_get(hass).async_get(coord.adapter_device_id)
        assert adapter.sw_version == "fw 1.2" and entry.unique_id == "58e6c5e3ba58"
        assert entry.runtime_data is coord  # keyed by the adapter's id without a reload
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_known_adapter_keeps_its_details_when_its_web_api_is_quiet(hass: HomeAssistant):
    from custom_components.ebus.device import register_adapter
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999})
    entry.add_to_hass(hass)
    device_id = register_adapter(hass, entry.entry_id, "1.2.3.4",
                                 {"versions": "fw 1.2", "chip": "ESP32-C3"})
    assert register_adapter(hass, entry.entry_id, "1.2.3.4", {}) == device_id
    adapter = dr.async_get(hass).async_get(device_id)
    assert (adapter.sw_version, adapter.model) == ("fw 1.2", "eBUS Adapter Shield (ESP32-C3)")
    # a new adapter without a web API gets the plain details
    other = MockConfigEntry(domain=DOMAIN, data={"host": "5.6.7.8", "port": 9999})
    other.add_to_hass(hass)
    plain = dr.async_get(hass).async_get(register_adapter(hass, other.entry_id, "5.6.7.8", {}))
    assert plain.model == "Enhanced adapter"


async def test_diagnostics_show_the_bus_without_secrets(hass: HomeAssistant):
    from custom_components.ebus.diagnostics import async_get_config_entry_diagnostics
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999},
                            version=1, minor_version=7)
    entry.add_to_hass(hass)
    p1, p2, p3, p4 = _setup_patches(_idle_link(), _found({0x08: (_HMU, _IDENT)}),
                                    AsyncMock(return_value={}))
    with p1, p2, p3, p4:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        diag = await async_get_config_entry_diagnostics(hass, entry)
        assert diag["devices"] == {"0x08": {"ident": "HMU00", "manufacturer": "Vaillant",
                                            "sw": "0902", "hw": "5103"}}
        assert diag["counts"]["devices"] == 1 and diag["counts"]["read_registers"] > 0
        assert _IDENT.serial not in str(diag)
        assert await hass.config_entries.async_unload(entry.entry_id)
