"""Coordinator: owns the adapter connection and runs the aioebus engine.

The engine does passive-first work on one connection -- it decodes the telegrams
other participants broadcast (free) and actively polls only the registers that
are due. The aioebus runner keeps that connection alive across drops, and the
coordinator publishes snapshots of the engine's value cache to the entities.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Final

import aiohttp
from aioebus.active import ReadError
from aioebus.catalog import Catalog
from aioebus.discovery import (
    BusSilent,
    Device,
    Discovery,
    discover,
    find_later,
    identify_later,
    recall,
    remember,
)
from aioebus.engine import EbusEngine
from aioebus.frame import BROADCAST
from aioebus.link import Link, connect
from aioebus.messages import MessageDef
from aioebus.passive import MessageRegistry
from aioebus.poll import PollScheduler
from aioebus.runner import EngineRunner
from aioebus.scan import Identification, ScanResult
from aioebus.values import CacheKey, Value, control_of, sent_value
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

from .ack_watch import AckWatch
from .adapter import fetch_status
from .cache import CacheStore
from .const import (
    ADDRESS_STORE,
    CONF_ALLOW_WRITE,
    CONF_CONFIG_DIR,
    CONF_DEEP_SCAN,
    CONF_SCAN_INTERVAL,
    DEFAULT_ALLOW_WRITE,
    DEFAULT_DEEP_SCAN,
    DEFAULT_SCAN_INTERVAL,
    DEFS_CACHE,
    DEVICE_STORE,
    DOMAIN,
    IDENT_STORE,
    PLAIN_READ_PRIORITY,
    STORE_VERSION,
)
from .device import (
    ADAPTER_IDENTIFIER,
    adopt_adapter_id,
    device_info,
    register_adapter,
    remove_devices,
)
from .entities import (
    CONTROLLER_POLL_GAP_S,
    control_class,
    controller_priority,
    cop_sources,
    presentation,
    select_polled,
)
from .ids import CONTROL_SUFFIX, control_uid, cop_uid, hot_water_uid, uid
from .translate import async_update_translations

_LOGGER = logging.getLogger(__name__)
_RESCOPE_COOLDOWN: Final = 2  # s; collapses a burst of registry changes into one rebuild
# pauses (s) before each runtime attempt at an identification missed at setup
_IDENT_RETRY_DELAYS: Final = (60, 240, 600, 900, 1800)
# a known device that missed the scan: looked for soon, then less often
_FIND_RETRY_DELAYS: Final = (15, 60, 240, 600, 900, 1800)
# the background scan behind a quick setup: soon, then again if the bus is silent
_VERIFY_DELAYS: Final = (20, 60, 300, 900)
_FETCH_TIMEOUT: Final = 15  # s per definition file from the CDN
_STALE_AFTER: Final = 600  # s without the link before values count as unavailable
_WEB_RETRY: Final = 600  # s between asks of an adapter whose web API did not answer

# the engine's value cache: (zz/circuit, message name, label) -> decoded value
type EbusCache = dict[CacheKey, object]


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
        self._entry = entry  # config_entry, typed as always present
        self.host = host
        self.port = port
        # the address and options this run was built with: only a change reloads
        self.entry_data = dict(entry.data)
        self.options = dict(entry.options)
        self.allow_write = entry.options.get(CONF_ALLOW_WRITE, DEFAULT_ALLOW_WRITE)
        self.deep_scan = entry.options.get(CONF_DEEP_SCAN, DEFAULT_DEEP_SCAN)
        cfg_dir = entry.options.get(CONF_CONFIG_DIR) or ""
        self.user_dir: Path | None = Path(cfg_dir) if cfg_dir.strip() else None
        self.devices: dict[int, ScanResult] = {}  # the scanned bus devices
        self.idents: dict[int, Identification] = {}  # per device: article/serial
        self.read_messages: list[MessageDef] = []
        self.write_messages: list[MessageDef] = []
        self.engine: EbusEngine | None = None
        self.acks: AckWatch | None = None  # set with the engine
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
        # monotonic time the web API was last asked in vain (setup asks first)
        self._web_asked = time.monotonic()
        self._runner: EngineRunner | None = None
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
        self._cache = CacheStore(hass, entry.entry_id)
        # addresses found beyond the known base (from a deep scan), so later setups
        # keep scanning them without repeating the full-space probe
        self._addr_store: Store[list[int]] = Store(
            hass, STORE_VERSION, f"{ADDRESS_STORE}{entry.entry_id}")
        # last identification read per device (it never changes)
        self._ident_store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, f"{IDENT_STORE}{entry.entry_id}")
        self._ident_known: dict[str, Any] = {}  # the store's content, kept current
        # the devices the last scan found: a later start sets up from them at once
        # and scans in the background (HA wants a quick setup)
        self._device_store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, f"{DEVICE_STORE}{entry.entry_id}")

    def uid(self, *parts: object) -> str:
        """An entity unique_id scoped to this config entry (see ids.py)."""
        return uid(self.uid_prefix, *parts)

    def control_class(self, msg: MessageDef) -> str:
        """"user", "installer" or "none" for a writable register (see entities)."""
        return control_class(self.catalog.definition(self.catalog.owner(msg)), msg.name)

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

    def controls(self, platform: str) -> list[tuple[Value, str]]:
        """The (value, class) of every control the platform provides. A register
        that no longer gets one loses its registry entry, and with writing
        switched off every control does (no 'no longer provided' orphans)."""
        registry = er.async_get(self.hass)
        if not self.allow_write:
            for ent in er.async_entries_for_config_entry(registry, self._entry.entry_id):
                if ent.domain == platform and ent.unique_id.endswith(CONTROL_SUFFIX):
                    registry.async_remove(ent.entity_id)
            return []
        out: list[tuple[Value, str]] = []
        for msg in self.write_messages:
            found = control_of(msg)
            if found is None or found[0] != platform:
                continue
            value = found[1]
            cls = self.control_class(msg)
            if cls == "none":
                self.remove_control(platform, msg, value.label)
            else:
                out.append((value, cls))
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
        """DeviceInfo for a bus device (hanging off the adapter hub)."""
        return device_info(zz, self.catalog.scan(zz), self.catalog.definition(zz),
                           self.adapter_device_id)

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
        if self._runner is not None:
            self._runner.close()

    def _known_devices(self) -> bool:
        """Whether this entry already has bus devices (from an earlier start)."""
        registry = dr.async_get(self.hass)
        return any(
            domain == DOMAIN and ident != ADAPTER_IDENTIFIER[1]
            for dev in dr.async_entries_for_config_entry(registry, self._entry.entry_id)
            for domain, ident in dev.identifiers)

    async def _open_link(self) -> Link:
        return await connect(self.host, self.port)

    async def _discover(self) -> None:
        # the engine runs from the start: discovery goes through it (one transaction
        # policy), and it decodes the bus passively once the definitions are known
        self.engine = EbusEngine(allow_write=self.allow_write)
        # other masters' commands that did not get through: logbook and issue
        engine = self.engine
        self.acks = AckWatch(self.hass, self._entry.entry_id, lambda: self.devices,
                             lambda: engine.lost_streaks)
        self.engine.on_ack_fault = self.acks.on_fault
        self._entry.async_on_unload(self.acks.close)
        self._runner = EngineRunner(self.engine, self._open_link, on_lost=self._on_link_lost)
        await self._runner.start()
        cached = await self._addr_store.async_load() or []
        stored = await self._ident_store.async_load() or {}
        known = _devices_from(await self._device_store.async_load(), stored)
        if known and not self.deep_scan:
            # quick setup from the devices found last time; the bus is scanned in
            # the background, and a new or changed device reloads the entry
            self._found = known
            self.devices = {zz: d.scan for zz, d in known.items()}
            self.idents = {zz: d.ident for zz, d in known.items() if d.ident is not None}
            self._ident_known = stored
            await self._finish_setup(translate_inline=False)
            self._entry.async_create_background_task(
                self.hass, self._verify_devices(cached, stored), name="ebus-verify")
            return
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
        # a phantom address of a unit found elsewhere; the former synthetic
        # 'Broadcast' device (its entities live on the adapter now)
        remove_devices(self.hass, self._entry.entry_id, [*found.dropped, BROADCAST])
        await self._save_discovery(found, cached, stored)
        if self.deep_scan:  # the option means "probe everything once": done
            self.hass.config_entries.async_update_entry(
                self._entry, options={**self._entry.options, CONF_DEEP_SCAN: False})
            self.options = dict(self._entry.options)
        await self._finish_setup(translate_inline=True)
        if found.missing:
            _LOGGER.info("eBUS: known device(s) %s did not answer the scan; looking again",
                         ", ".join(f"{a:#04x}" for a in found.missing))
            self._entry.async_create_background_task(
                self.hass, self._late_find(found.missing), name="ebus-find")

    async def _finish_setup(self, *, translate_inline: bool) -> None:
        """Everything after the device list is known: adapter device, definitions,
        names, polling and the value cache."""
        entry_id = self._entry.entry_id
        # register the adapter as the hub device: scanned devices hang off it
        # (via_device_id) and the broadcast entities belong to it. Enrich it from
        # the adapter shield's own web API when present (real firmware/hardware +
        # a link straight to its web UI)
        await self._ask_web()
        self.adapter_device_id = register_adapter(
            self.hass, entry_id, self.host, self.adapter_status)
        await self.async_load_definitions()
        # generate localized entity names on the user's machine from the upstream
        # CDN (never bundled): best-effort, so a CDN hiccup just leaves names
        # English. A known installation has them already: refresh in the background
        if translate_inline:
            await self._translate()
        else:
            self._entry.async_create_background_task(
                self.hass, self._translate(), name="ebus-translate")
        assert self.engine is not None
        self.engine.registry = MessageRegistry(self.catalog.all_messages())
        self.engine.scheduler = self._build_scheduler()
        await self._cache.async_restore(self.engine)
        # re-scope polling when one of our entities is created, enabled or disabled
        self._entry.async_on_unload(self._rescope.async_cancel)
        self._entry.async_on_unload(self.hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED, self._on_registry_update))
        self._entry.async_create_background_task(
            self.hass, self._late_identify(), name="ebus-identify")

    async def _translate(self) -> None:
        try:
            await async_update_translations(self.hass, self._entry.entry_id, self.catalog)
        except Exception as err:  # noqa: BLE001 - names are cosmetic, never block setup
            _LOGGER.debug("eBUS: translation generation skipped: %s", err)

    async def _save_discovery(self, found: Discovery, cached: list[Any],
                              stored: dict[str, Any]) -> None:
        if found.extra_addresses != sorted(a for a in cached if isinstance(a, int)):
            await self._addr_store.async_save(found.extra_addresses)
        if found.remembered != stored:
            await self._ident_store.async_save(found.remembered)
        await self._device_store.async_save(
            {f"{zz:02x}": _scan_record(d.scan) for zz, d in found.devices.items()})

    async def _verify_devices(self, cached: list[Any], stored: dict[str, Any]) -> None:
        """Scan the bus behind a quick setup. A new device, or one that answers
        as another unit now, reloads the entry; a known one that misses this scan
        stays (a busy bus) and is looked for again."""
        assert self.engine is not None
        for delay in _VERIFY_DELAYS:
            await asyncio.sleep(delay)
            try:
                found = await discover(self.engine, extra_addresses=cached, remembered=stored)
            except (BusSilent, ConnectionError) as err:
                _LOGGER.debug("eBUS: background scan not possible yet: %s", err)
                continue
            break
        else:
            return
        changed = sorted(
            zz for zz, d in found.devices.items()
            if zz not in self.devices or _scan_record(self.devices[zz]) != _scan_record(d.scan))
        missing = [zz for zz in self.devices if zz not in found.devices]
        merged = Discovery({**self._found, **found.devices}, found.extra_addresses,
                           found.remembered, found.dropped, missing)
        await self._save_discovery(merged, cached, stored)
        if changed or found.dropped:
            _LOGGER.info("eBUS: devices changed on the bus (%s); reloading",
                         ", ".join(f"{a:#04x}" for a in changed) or "duplicate addresses")
            self.hass.config_entries.async_schedule_reload(self._entry.entry_id)
            return
        if missing:
            self._entry.async_create_background_task(
                self.hass, self._late_find(missing), name="ebus-find")

    async def _ask_web(self) -> bool:
        """Ask the adapter's web API for its status; True when it answered (the
        first time, it also keys the entry by the adapter's own id)."""
        status = await fetch_status(self.hass, self.host)
        if not status:
            self._web_asked = time.monotonic()
            return False
        self.adapter_status = status
        if not self._has_web:
            self._has_web = True
            adopt_adapter_id(self.hass, self._entry, status.get("id"))
        return True

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

    def controller_polls_per_minute(self) -> float | None:
        """Our polls of the system controller(s) in the last minute."""
        if self.engine is None:
            return None
        now = time.monotonic()
        rates = [self.engine.meter.polls_per_minute(now, int(zz, 16))
                 for zz in {z.zz for z in self.catalog.zones()}]
        known = [r for r in rates if r is not None]
        return sum(known) if known else None

    def foreign_unanswered_per_minute(self) -> float | None:
        """Other masters' reads of the devices on this bus that stayed
        unanswered in the last minute (a probe of an empty address is left out)."""
        if self.engine is None:
            return None
        return self.engine.meter.unanswered_per_minute(time.monotonic(), set(self.devices))

    def ack_faults_per_minute(self) -> float | None:
        """Telegrams (the devices' and ours) to the devices on this bus whose
        acknowledgment failed in the last minute."""
        if self.engine is None or self.acks is None:
            return None
        return self.engine.meter.ack_faults_per_minute(time.monotonic(), self.acks.addresses())

    async def _late_find(self, missing: list[int]) -> None:
        """A device known from an earlier start that missed the scan (a busy
        bus): once it answers, reload so it gets its definitions and entities."""
        if self.engine is None:
            return
        async for device in find_later(self.engine, missing, _FIND_RETRY_DELAYS):
            _LOGGER.info("eBUS: device %#04x answered later; reloading", device.address)
            self.hass.config_entries.async_schedule_reload(self._entry.entry_id)
            return

    def _derived_entities(self) -> list[tuple[str, tuple[MessageDef | None, ...]]]:
        """The unique ids of the entities that are no single register, with the
        registers they show."""
        prefix = self.uid_prefix
        out: list[tuple[str, tuple[MessageDef | None, ...]]] = [
            (hot_water_uid(prefix, w.zz),
             (w.target_read, w.current, w.mode_read, w.special_read))
            for w in self.catalog.hot_water()]
        for address in self.catalog.addresses:
            if (sources := cop_sources(self.catalog.messages(address))) is not None:
                out.append((cop_uid(prefix, f"{address:02x}"), sources))
        return out

    def _build_scheduler(self) -> PollScheduler:
        """Poll only the read registers that back an enabled entity. Before any
        entity is registered (fresh install) poll every read, so values arrive
        and the entities can be created; after that, a disabled entity's register
        is dropped from the rotation (unless another enabled entity needs it)."""
        reg = er.async_get(self.hass)
        entries = er.async_entries_for_config_entry(reg, self._entry.entry_id)
        if not entries:
            polled = self.read_messages  # the scheduler takes the true reads
        else:
            enabled = {e.unique_id for e in entries if e.disabled_by is None}
            polled = select_polled(
                self.read_messages, enabled,
                self.catalog.zones(), self.uid_prefix, self._derived_entities())
        # a system controller (it holds the heating zones) shares the bus with
        # its gateway: poll it slowly and with a gap, so the gateway's reads get
        # through
        controllers = {z.zz.lower() for z in self.catalog.zones()}

        def priority_for(msg: MessageDef, priority: int) -> int:
            return controller_priority(msg, priority) if msg.zz.lower() in controllers else priority

        return PollScheduler(polled, base_interval=self._base_interval,
                             default_priority=PLAIN_READ_PRIORITY,
                             address_gaps=dict.fromkeys(controllers, CONTROLLER_POLL_GAP_S),
                             priority_for=priority_for)

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
        if entity is None or entity.config_entry_id != self._entry.entry_id:
            return  # another integration's entity
        self._rescope.async_schedule_call()

    def _on_link_lost(self) -> None:
        """The runner lost the link (it reconnects by itself): mark the outage."""
        if self._link_down_since is None:
            self._link_down_since = time.monotonic()
        # once per outage (a bus without SYN generator, e.g. heating off, would
        # otherwise log every ~40 s); _async_update_data logs the recovery
        self._log_once("eBUS: link to %s:%s lost, reconnecting", self.host, self.port)

    def _log_once(self, msg: str, *args: object) -> None:
        if self._link_lost_logged:
            _LOGGER.debug(msg, *args)
        else:
            _LOGGER.warning(msg, *args)
            self._link_lost_logged = True

    async def async_write(self, msg: MessageDef, value: object) -> None:
        """Write one value to a single-field register; the entity then shows what
        the device holds (read back, else the written value). Errors surface as
        HomeAssistantError."""
        sent = sent_value(msg)
        if self.engine is None or not self.allow_write or sent is None:
            raise HomeAssistantError(f"eBUS: {msg.name} is not writable")
        if isinstance(value, float) and value.is_integer():
            value = int(value)  # a number entity gives floats
        try:
            outcome = await self.engine.write_confirmed(
                msg, {sent.field.name: value}, read_back=self.catalog.read_twin(msg))
        except (TimeoutError, ReadError, ValueError, OSError, NotImplementedError) as err:
            raise HomeAssistantError(f"eBUS write to {msg.name} failed: {err}") from err
        self.async_set_updated_data(dict(self.engine.cache))
        if outcome.uncertain is not None:
            shown = ("the shown value was read back" if outcome.read_back
                     else "it could not be read back")
            raise HomeAssistantError(
                f"eBUS write to {msg.name} not confirmed ({outcome.uncertain}); it may "
                f"have been applied -- {shown}") from outcome.uncertain

    async def async_read_now(self, *msgs: MessageDef | None) -> None:
        """Read registers now (e.g. a setpoint a write changed); best effort,
        the next poll reads them anyway."""
        if self.engine is None:
            return
        read = False
        for msg in msgs:
            if msg is None:
                continue
            try:
                await self.engine.read(msg)
            except (TimeoutError, ReadError, ValueError, OSError) as err:
                _LOGGER.debug("eBUS: %s not read now: %s", msg.name, err)
                continue
            read = True
        if read:
            self.async_set_updated_data(dict(self.engine.cache))

    async def _async_update_data(self) -> EbusCache:
        if self.engine is None:
            raise UpdateFailed("engine not started")
        if self._link_lost_logged and self.engine.stats.connected:
            _LOGGER.info("eBUS: link to %s:%s restored", self.host, self.port)
            self._link_lost_logged = False
        if self.engine.stats.connected:
            self._link_down_since = None
        # refresh the adapter's web status for its diagnostic entities; one that
        # did not answer (no web UI, or slow at setup) is asked again now and then
        if self._has_web:
            self.adapter_status = await fetch_status(self.hass, self.host)
        elif time.monotonic() - self._web_asked > _WEB_RETRY and await self._ask_web():
            register_adapter(self.hass, self._entry.entry_id, self.host,
                             self.adapter_status)
        if self.acks is not None:
            self.acks.tick()  # clear an issue whose commands arrive again
        # persist the cache (throttled) so a restart can warm-start from it
        self._cache.schedule_save(self.engine)
        return dict(self.engine.cache)

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        if self.engine is not None:
            await self._cache.async_save(self.engine)  # final flush
        self._close()


def _scan_record(scan: ScanResult) -> dict[str, Any]:
    """A device's scan answer as stored (JSON-ready)."""
    return {"mf": scan.mf, "manufacturer": scan.manufacturer, "ident": scan.ident,
            "sw": scan.sw, "hw": scan.hw, "sw_num": scan.sw_num, "hw_num": scan.hw_num}


def _devices_from(data: object, idents: dict[str, Any]) -> dict[int, Device]:
    """The devices of the last scan, with their remembered identification; a
    malformed record is skipped (a hand-edited store)."""
    out: dict[int, Device] = {}
    if not isinstance(data, dict):
        return out
    for key, rec in data.items():
        try:
            zz = int(key, 16)
            scan = ScanResult(int(rec["mf"]), str(rec["manufacturer"]), str(rec["ident"]),
                              str(rec["sw"]), str(rec["hw"]),
                              rec["sw_num"] if isinstance(rec["sw_num"], int) else None,
                              rec["hw_num"] if isinstance(rec["hw_num"], int) else None)
        except (KeyError, TypeError, ValueError):
            continue
        out[zz] = Device(zz, scan, recall(scan, idents.get(key)))
    return out
