"""Coordinator: owns the adapter connection and runs the aioebus engine.

The engine does passive-first work on one connection -- it decodes the telegrams
other participants broadcast (free) and actively polls only the registers that
are due. A supervisor task keeps that connection alive across drops, and the
coordinator publishes snapshots of the engine's value cache to the entities.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any, Final

import aiohttp
from aioebus.active import ReadError, WriteUncertain
from aioebus.catalog import BROADCAST_SCAN, Catalog
from aioebus.discovery import BusSilent, Device, discover, identify_later, remember
from aioebus.engine import EbusEngine, value_key
from aioebus.messages import MessageDef
from aioebus.passive import MessageRegistry
from aioebus.poll import PollScheduler
from aioebus.scan import Identification, ScanResult
from aioebus.values import Value, control_of, values_of
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .adapter import fetch_status
from .const import (
    ADDRESS_STORE,
    BROADCAST_ADDRESS,
    CACHE_STORE,
    CONF_ALLOW_WRITE,
    CONF_CONFIG_DIR,
    CONF_DEEP_SCAN,
    CONF_SCAN_INTERVAL,
    DEFAULT_ALLOW_WRITE,
    DEFAULT_DEEP_SCAN,
    DEFAULT_SCAN_INTERVAL,
    DEFS_CACHE,
    DOMAIN,
    IDENT_STORE,
    PLAIN_READ_PRIORITY,
    SOURCE_ADDRESS,
    STORE_VERSION,
)
from .device import ADAPTER_IDENTIFIER, adapter_registry_info, device_info
from .entities import control_class, presentation, select_polled
from .ids import CONTROL_SUFFIX, control_uid, uid
from .translate import async_update_translations

_LOGGER = logging.getLogger(__name__)
_RECONNECT_BACKOFF: Final = 10  # seconds between reconnect attempts
_CONNECT_TIMEOUT: Final = 10  # s to open the adapter connection
_RECV_TIMEOUT: Final = 30  # s without a single byte before the link counts as dead
_RESCOPE_COOLDOWN: Final = 2  # s; collapses a burst of registry changes into one rebuild
# pauses (s) before each runtime attempt at an identification missed at setup
_IDENT_RETRY_DELAYS: Final = (60, 240, 600, 900, 1800)
_CACHE_SAVE_DELAY: Final = 5  # s; throttle persisting the cache (below the
# shortest update interval: each update re-arms the delay, a longer one never fires)
_FETCH_TIMEOUT: Final = 15  # s per definition file from the CDN
_STALE_AFTER: Final = 600  # s without the link before values count as unavailable
_JSON_SCALARS: Final = (str, int, float, bool, type(None))

type SendFn = Callable[[bytes], Awaitable[None]]
type RecvFn = Callable[[], Awaitable[bytes]]
# the engine's value cache: (zz/circuit, message name, field) -> decoded value
type EbusCache = dict[tuple[str, str, str], object]


class EbusCoordinator(DataUpdateCoordinator[EbusCache]):
    """Supervises the adapter connection + engine, publishes cache snapshots."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, host: str, port: int
    ) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass, _LOGGER, name="eBUS",
            update_interval=timedelta(seconds=interval),
            config_entry=entry,
        )
        self.host = host
        self.port = port
        self.allow_write = entry.options.get(CONF_ALLOW_WRITE, DEFAULT_ALLOW_WRITE)
        self.deep_scan = entry.options.get(CONF_DEEP_SCAN, DEFAULT_DEEP_SCAN)
        cfg_dir = entry.options.get(CONF_CONFIG_DIR) or ""
        self.user_dir: Path | None = Path(cfg_dir) if cfg_dir.strip() else None
        self.devices: dict[int, ScanResult] = {}
        self.idents: dict[int, Identification] = {}  # per device: article/serial
        self.read_messages: list[MessageDef] = []
        self.write_messages: list[MessageDef] = []
        self.engine: EbusEngine | None = None
        # the adapter shield's own web API (firmware, Wi-Fi, uptime); {} when the
        # adapter has no web UI or is unreachable
        self.adapter_status: dict[str, Any] = {}
        self.adapter_device_id: str | None = None  # registry id of the hub device
        # entity unique_ids start with the entry id, so two adapters (with devices
        # on the same bus addresses) never collide
        self.uid_prefix = entry.entry_id
        self._link_lost_logged = False
        self._link_down_since: float | None = None  # monotonic; None while up
        self._has_web = False
        self._writer: asyncio.StreamWriter | None = None
        self._supervisor: asyncio.Task[None] | None = None
        self._link_task: asyncio.Task[None] | None = None  # the first engine run
        self._found: dict[int, Device] = {}  # discovered devices (for late idents)
        # the devices' definitions: bundled, user files, or ebusd CSVs from the CDN
        # (downloaded on this machine only, cached under .storage)
        self.catalog = Catalog(
            user_dir=self.user_dir, cache_dir=Path(hass.config.path(".storage", DEFS_CACHE)),
            fetch=self._fetch_text)
        self._base_interval = interval
        # collapses a burst of registry changes (a platform registering its
        # entities) into one poll-set rebuild
        self._rescope: Debouncer[None] = Debouncer(
            hass, _LOGGER, cooldown=_RESCOPE_COOLDOWN, immediate=False,
            function=self.async_rescope_polling)
        # persisted value cache, so entities show their last value right after a
        # restart instead of being unavailable until the bus is read again
        self._store: Store[dict[str, object]] = Store(
            hass, STORE_VERSION, f"{CACHE_STORE}{entry.entry_id}")
        # addresses found beyond the known base (from a deep scan), so later setups
        # keep scanning them without repeating the full-space probe
        self._addr_store: Store[list[int]] = Store(
            hass, STORE_VERSION, f"{ADDRESS_STORE}{entry.entry_id}")
        # last identification read per device (it never changes)
        self._ident_store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, f"{IDENT_STORE}{entry.entry_id}")
        self._ident_known: dict[str, Any] = {}  # the store's content, kept current

    def uid(self, *parts: object) -> str:
        """An entity unique_id scoped to this config entry (see ids.py)."""
        return uid(self.uid_prefix, *parts)

    def control_class(self, msg: MessageDef) -> str:
        """"user", "installer" or "none" for a writable register (see entities)."""
        return control_class(self.catalog.definition(int(msg.zz, 16)), msg.name)

    def remove_control(self, domain: str, msg: MessageDef, label: str) -> None:
        """Drop the registry entry of a control this register no longer gets
        (registered by a version before the classes existed)."""
        registry = er.async_get(self.hass)
        if entity_id := registry.async_get_entity_id(
                domain, DOMAIN, control_uid(self.uid_prefix, msg, label)):
            registry.async_remove(entity_id)

    @property
    def bus_stale(self) -> bool:
        """The link has been down too long for the last bus values to count as
        current: entities showing them are unavailable (the adapter's own are not)."""
        down = self._link_down_since
        return down is not None and time.monotonic() - down > _STALE_AFTER

    def controls(self, platform: str) -> list[tuple[MessageDef, Value, str]]:
        """The (message, field, class) of every control the platform provides. A
        register that no longer gets one loses its registry entry, and with
        writing switched off every control does (no 'no longer provided' orphans)."""
        registry = er.async_get(self.hass)
        if not self.allow_write:
            for ent in er.async_entries_for_config_entry(registry, self.config_entry.entry_id):
                if ent.domain == platform and ent.unique_id.endswith(CONTROL_SUFFIX):
                    registry.async_remove(ent.entity_id)
            return []
        out: list[tuple[MessageDef, Value, str]] = []
        for msg in self.write_messages:
            found = control_of(msg)
            if found is None or found[0] != platform:
                continue
            field = found[1]
            cls = self.control_class(msg)
            if cls == "none":
                self.remove_control(platform, msg, field.label)
            else:
                out.append((msg, field, cls))
        return out

    async def async_load_definitions(self) -> None:
        """Read (or download) every device's definition; from here on the catalog
        answers from memory and the message lists are set."""
        await self.hass.async_add_executor_job(presentation)
        await self.catalog.load(self.devices, self.idents)
        self.read_messages = self.catalog.device_messages("r")
        self.write_messages = self.catalog.device_messages("w") if self.allow_write else []

    async def _fetch_text(self, url: str) -> str | None:
        """A file from the CDN for the catalog: "" when the CDN says it is not
        there (final), None when it cannot be reached (the catalog then keeps its
        cached copy and tries again another time)."""
        try:
            async with (asyncio.timeout(_FETCH_TIMEOUT),
                        async_get_clientsession(self.hass).get(url) as resp):
                if resp.status == 200:
                    return await resp.text()
                return "" if resp.status == 404 else None
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:  # ValueError: not UTF-8
            _LOGGER.debug("eBUS: %s not fetched: %s", url, err)
            return None

    def device_info(self, zz: int) -> DeviceInfo:
        """DeviceInfo for a scanned device (hanging off the adapter hub)."""
        return device_info(zz, self.devices.get(zz), self.catalog.definition(zz),
                           self.adapter_device_id)

    async def _connect(self) -> tuple[SendFn, RecvFn]:
        async with asyncio.timeout(_CONNECT_TIMEOUT):
            reader, writer = await asyncio.open_connection(self.host, self.port)
        self._writer = writer

        async def send(data: bytes) -> None:
            writer.write(data)
            await writer.drain()

        async def recv() -> bytes:
            # a live bus streams SYN symbols continuously, so silence means a dead
            # link (adapter rebooted, half-open TCP): time out and reconnect
            async with asyncio.timeout(_RECV_TIMEOUT):
                data = await reader.read(4096)
            if not data:  # EOF; read() would return b"" at once forever
                raise ConnectionResetError("adapter closed the connection")
            return data

        return send, recv

    async def async_discover(self) -> None:
        """Connect, scan the bus, choose entities to expose and start the engine.
        Raises ConfigEntryNotReady when the adapter is unreachable, so Home
        Assistant retries the setup instead of leaving the entry failed."""
        try:
            await self._discover()
        except (OSError, TimeoutError) as err:
            self._close()
            raise ConfigEntryNotReady(
                f"eBUS adapter {self.host}:{self.port} not reachable: {err}") from err
        except BaseException:
            self._close()
            raise

    def _close(self) -> None:
        if self.engine is not None:
            self.engine.stop()
        if self._link_task is not None:
            self._link_task.cancel()
        if self._writer is not None:
            self._writer.close()

    def _known_devices(self) -> bool:
        """Whether this entry already has bus devices (from an earlier start)."""
        registry = dr.async_get(self.hass)
        return any(
            domain == DOMAIN and ident != ADAPTER_IDENTIFIER[1]
            for dev in dr.async_entries_for_config_entry(registry, self.config_entry.entry_id)
            for domain, ident in dev.identifiers)

    async def _discover(self) -> None:
        send, recv = await self._connect()
        # the engine runs from the start: discovery goes through it (one transaction
        # policy), and it decodes the bus passively once the definitions are known
        self.engine = EbusEngine(send, recv, MessageRegistry([]),
                                 qq=SOURCE_ADDRESS, allow_write=self.allow_write)
        self._link_task = asyncio.create_task(self.engine.run(), name="ebus-link")
        cached = await self._addr_store.async_load() or []
        stored = await self._ident_store.async_load() or {}
        if self.deep_scan:
            _LOGGER.info("eBUS: deep scan of the whole address space")
        try:
            found = await discover(self.engine, extra_addresses=cached, deep=self.deep_scan,
                                   remembered=stored)
        except BusSilent as err:
            raise ConfigEntryNotReady("no eBUS traffic yet (is the heating on?)") from err
        if not found.devices and self._known_devices():
            # the bus is alive but nobody answered (devices still booting): try
            # again later rather than dropping every device until a reload
            raise ConfigEntryNotReady("no eBUS device answered the scan")
        self.devices = {zz: d.scan for zz, d in found.devices.items()}
        self.idents = {zz: d.ident for zz, d in found.devices.items() if d.ident is not None}
        self._found = found.devices
        self._ident_known = found.remembered
        registry = dr.async_get(self.hass)
        for drop in found.dropped:  # a phantom address of a unit found elsewhere
            dev = registry.async_get_device_by_identifier(
                (DOMAIN, f"{drop:02x}"), self.config_entry.entry_id)
            if dev is not None:
                registry.async_remove_device(dev.id)
        if found.extra_addresses != sorted(a for a in cached if isinstance(a, int)):
            await self._addr_store.async_save(found.extra_addresses)
        if found.remembered != stored:
            await self._ident_store.async_save(found.remembered)
        if self.deep_scan:  # the option means "probe everything once": done
            self.hass.config_entries.async_update_entry(
                self.config_entry, options={**self.config_entry.options, CONF_DEEP_SCAN: False})
        # the broadcast circuit is not a scanned device: inject it so bus
        # broadcasts (outside temperature, date/time) become entities too
        self.devices[BROADCAST_ADDRESS] = BROADCAST_SCAN
        # register the adapter as the hub device: scanned devices hang off it
        # (via_device_id) and the broadcast entities belong to it. Enrich it from
        # the adapter shield's own web API when present (real firmware/hardware +
        # a link straight to its web UI)
        self.adapter_status = await fetch_status(self.hass, self.host)
        self._has_web = bool(self.adapter_status)
        self._adopt_adapter_id()
        self.adapter_device_id = registry.async_get_or_create(
            **adapter_registry_info(
                self.config_entry.entry_id, self.host, self.adapter_status)).id
        # drop the former synthetic 'Broadcast' device: its entities now live on
        # the adapter, so it lingers as an empty shell from older versions
        stale = registry.async_get_device_by_identifier(
            (DOMAIN, f"{BROADCAST_ADDRESS:02x}"), self.config_entry.entry_id)
        if stale is not None:
            registry.async_remove_device(stale.id)
        await self.async_load_definitions()
        # generate localized entity names on the user's machine from the upstream
        # CDN (never bundled): best-effort, so a CDN hiccup just leaves names English
        try:
            await async_update_translations(self.hass, self.config_entry.entry_id, self.catalog)
        except Exception as err:  # noqa: BLE001 - names are cosmetic, never block setup
            _LOGGER.debug("eBUS: translation generation skipped: %s", err)
        self.engine.registry = MessageRegistry(self.catalog.all_messages())
        self.engine.scheduler = self._build_scheduler()
        await self._warm_start_cache()
        # re-scope polling when one of our entities is created, enabled or disabled
        self.config_entry.async_on_unload(self._rescope.async_cancel)
        self.config_entry.async_on_unload(self.hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED, self._on_registry_update))
        self._supervisor = self.config_entry.async_create_background_task(
            self.hass, self._run_forever(), name="ebus-supervisor")
        self.config_entry.async_create_background_task(
            self.hass, self._late_identify(), name="ebus-identify")
        _LOGGER.info("eBUS: %d device(s), %d read / %d write register(s)",
                     len(self.devices), len(self.read_messages), len(self.write_messages))

    async def _late_identify(self) -> None:
        """Read the identification of Vaillant devices that did not give it at
        setup (e.g. behind a slow bus coupler), a few times with growing pauses.
        Once read it is remembered for later starts."""
        if self.engine is None:
            return
        async for device in identify_later(self.engine, self._found, _IDENT_RETRY_DELAYS):
            assert device.ident is not None
            self.idents[device.address] = device.ident
            self._ident_known[f"{device.address:02x}"] = remember(device)
            await self._ident_store.async_save(self._ident_known)
            _LOGGER.debug("ident %#04x read at runtime", device.address)
            self.async_update_listeners()  # the sensor platform adds its entities

    def _adopt_adapter_id(self) -> None:
        """Key the entry by the adapter's own id (its MAC) once it is known, so a
        new IP address is recognised as the same adapter instead of a new one."""
        adapter_id = self.adapter_status.get("id")
        entry = self.config_entry
        if not isinstance(adapter_id, str) or not adapter_id or entry.unique_id == adapter_id:
            return
        if any(e.unique_id == adapter_id
               for e in self.hass.config_entries.async_entries(DOMAIN) if e is not entry):
            return  # another entry already claims it; leave both untouched
        self.hass.config_entries.async_update_entry(entry, unique_id=adapter_id)

    def _build_scheduler(self) -> PollScheduler:
        """Poll only the read registers that back an enabled entity. Before any
        entity is registered (fresh install) poll every read, so values arrive
        and the entities can be created; after that, a disabled entity's register
        is dropped from the rotation (unless another enabled entity needs it)."""
        reg = er.async_get(self.hass)
        entries = er.async_entries_for_config_entry(reg, self.config_entry.entry_id)
        if not entries:
            polled: list[MessageDef] = [
                m for m in self.read_messages if m.is_polled_read]
        else:
            enabled = {e.unique_id for e in entries if e.disabled_by is None}
            polled = select_polled(
                self.read_messages, enabled,
                self.catalog.zones(), self.uid_prefix)
        return PollScheduler(polled, base_interval=self._base_interval,
                             default_priority=PLAIN_READ_PRIORITY)

    @callback
    def async_rescope_polling(self) -> None:
        """Rebuild the poll set from the entity registry as it is now. Registers
        that stay polled keep their timing, so a rebuild causes no poll burst."""
        if self.engine is None:
            return
        scheduler = self._build_scheduler()
        if self.engine.scheduler is not None:
            scheduler.carry_from(self.engine.scheduler)
        self.engine.scheduler = scheduler

    @callback
    def _on_registry_update(self, event: Event[Any]) -> None:
        """Re-scope polling when one of our entities is created (e.g. a climate
        zone that appears once its data arrived), enabled or disabled."""
        action = event.data.get("action")
        if action == "update":
            if "disabled_by" not in event.data.get("changes", {}):
                return
        elif action != "create":
            return
        entity = er.async_get(self.hass).async_get(event.data["entity_id"])
        if entity is None or entity.config_entry_id != self.config_entry.entry_id:
            return  # another integration's entity
        self._rescope.async_schedule_call()

    async def _run_forever(self) -> None:
        """Run the engine, reconnecting with backoff if the link drops."""
        assert self.engine is not None
        first = self._link_task  # started for discovery
        while True:
            try:
                if first is not None:
                    first, run = None, first
                    await run
                else:
                    await self.engine.run()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - keep the supervisor alive
                _LOGGER.debug("engine stopped: %s", err)
            if self._link_down_since is None:
                self._link_down_since = time.monotonic()
            # once per outage (a bus without SYN generator, e.g. heating off, would
            # otherwise log every ~40 s); _async_update_data logs the recovery
            self._log_once("eBUS: link to %s:%s lost, reconnecting", self.host, self.port)
            if self._writer is not None:
                self._writer.close()
            await asyncio.sleep(_RECONNECT_BACKOFF)
            try:
                send, recv = await self._connect()
                self.engine.attach(send, recv)
                _LOGGER.debug("eBUS: reconnected to %s:%s", self.host, self.port)
            except OSError as err:
                _LOGGER.debug("eBUS: reconnect failed: %s", err)

    def _log_once(self, msg: str, *args: object) -> None:
        if self._link_lost_logged:
            _LOGGER.debug(msg, *args)
        else:
            _LOGGER.warning(msg, *args)
            self._link_lost_logged = True

    async def async_write(self, msg: MessageDef, value: object) -> None:
        """Write one value to a single-field register, then read it back so the
        entity shows what the device actually holds (if it cannot be read back,
        the written value is shown). Errors surface as HomeAssistantError."""
        fields = values_of(msg)
        if self.engine is None or not self.allow_write or len(fields) != 1:
            raise HomeAssistantError(f"eBUS: {msg.name} is not writable")
        field = fields[0]
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        uncertain: WriteUncertain | None = None
        try:
            await self.engine.write(msg, {field.field.name: value})
        except WriteUncertain as err:  # sent, but unconfirmed: may have been applied
            uncertain = err
        except (TimeoutError, ReadError, ValueError, OSError, NotImplementedError) as err:
            raise HomeAssistantError(f"eBUS write to {msg.name} failed: {err}") from err
        twin = next((m for m in self.read_messages if m.zz == msg.zz
                     and m.name == msg.name and m.is_polled_read), None)
        read_back = False
        try:
            if twin is None:
                raise ReadError("no readable twin")
            await self.engine.read(twin)  # stores the read value in the cache
            read_back = True
        except (TimeoutError, ReadError, ValueError, OSError) as err:
            _LOGGER.debug("eBUS: read-back of %s skipped: %s", msg.name, err)
            if uncertain is None:  # confirmed: show what was written
                self.engine.cache[value_key(msg, field.label)] = value
        self.async_set_updated_data(dict(self.engine.cache))
        if uncertain is not None:
            shown = "the shown value was read back" if read_back else "it could not be read back"
            raise HomeAssistantError(
                f"eBUS write to {msg.name} not confirmed ({uncertain}); it may have "
                f"been applied -- {shown}") from uncertain

    async def _async_update_data(self) -> EbusCache:
        if self.engine is None:
            raise UpdateFailed("engine not started")
        if self._link_lost_logged and self.engine.stats.connected:
            _LOGGER.info("eBUS: link to %s:%s restored", self.host, self.port)
            self._link_lost_logged = False
        if self.engine.stats.connected:
            self._link_down_since = None
        # refresh the adapter's web status for its diagnostic entities (only when
        # the adapter actually has a web UI, so a plain adapter isn't probed)
        if self._has_web:
            self.adapter_status = await fetch_status(self.hass, self.host)
        # persist the cache (throttled) so a restart can warm-start from it
        self._store.async_delay_save(self._serialize_cache, _CACHE_SAVE_DELAY)
        return dict(self.engine.cache)

    def _serialize_cache(self) -> dict[str, object]:
        """The value cache as JSON: [[["zz","name","field"], value], ...].
        Only scalar values are kept, so the store always stays serialisable."""
        if self.engine is None:
            return {"values": []}
        return {
            "values": [
                [list(key), value]
                for key, value in self.engine.cache.items()
                if isinstance(value, _JSON_SCALARS)
            ]
        }

    async def _warm_start_cache(self) -> None:
        """Seed the engine cache from the last persisted values, so entities are
        available with their last value immediately after a restart."""
        if self.engine is None:
            return
        try:
            stored = await self._store.async_load()
        except Exception as err:  # noqa: BLE001 - a bad cache file must not block setup
            _LOGGER.debug("eBUS: could not load value cache: %s", err)
            return
        if not stored:
            return
        values = stored.get("values")
        if not isinstance(values, list):
            return
        # drop values of registers a scanned device no longer defines (retired or
        # renamed), else they are carried along in the store forever; a device
        # that missed this scan keeps its values for the next start
        known = {(m.zz or m.circuit, m.name) for m in self.engine.registry.messages}
        scanned = {zz for zz, _name in known}
        for item in values:
            if (isinstance(item, list) and len(item) == 2
                    and isinstance(item[0], list) and len(item[0]) == 3):
                k = item[0]
                if str(k[0]) not in scanned or (str(k[0]), str(k[1])) in known:
                    self.engine.cache[(str(k[0]), str(k[1]), str(k[2]))] = item[1]
        _LOGGER.debug("eBUS: warm-started %d cached value(s)", len(self.engine.cache))

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        if self.engine is not None:
            await self._store.async_save(self._serialize_cache())  # final flush
            self.engine.stop()
        if self._supervisor is not None:
            self._supervisor.cancel()
        if self._link_task is not None:
            self._link_task.cancel()
        if self._writer is not None:
            self._writer.close()
