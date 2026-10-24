"""Coordinator: owns the adapter connection and runs the aioebus engine.

The engine does passive-first work on one connection -- it decodes the telegrams
other participants broadcast (free) and actively polls only the registers that
are due. A supervisor task keeps that connection alive across drops, and the
coordinator publishes snapshots of the engine's value cache to the entities.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any, Final

from aioebus import __version__ as AIOEBUS_VERSION
from aioebus.active import ActiveReader
from aioebus.engine import EbusEngine
from aioebus.messages import MessageDef
from aioebus.passive import MessageRegistry
from aioebus.poll import PollScheduler
from aioebus.scan import ScanResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BROADCAST_ADDRESS,
    BROADCAST_IDENT,
    CONF_ALLOW_WRITE,
    CONF_CONFIG_DIR,
    CONF_SCAN_INTERVAL,
    DEFAULT_ALLOW_WRITE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLAIN_READ_PRIORITY,
    SCAN_ADDRESSES,
    SOURCE_ADDRESS,
)
from .device import adapter_identifier
from .library import (
    all_messages,
    climate_zones,
    device_messages,
    select_polled,
    timer_schedules,
)

_LOGGER = logging.getLogger(__name__)
_RECONNECT_BACKOFF: Final = 10  # seconds between reconnect attempts
_CACHE_STORAGE_VERSION: Final = 1
_CACHE_SAVE_DELAY: Final = 20  # s; throttle persisting the cache (below the
# poll interval so the delayed write actually fires between updates)
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
        cfg_dir = entry.options.get(CONF_CONFIG_DIR) or ""
        self.user_dir: Path | None = Path(cfg_dir) if cfg_dir.strip() else None
        self.devices: dict[int, ScanResult] = {}
        self.read_messages: list[MessageDef] = []
        self.write_messages: list[MessageDef] = []
        self.engine: EbusEngine | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._supervisor: asyncio.Task[None] | None = None
        self._base_interval = interval
        # persisted value cache, so entities show their last value right after a
        # restart instead of being unavailable until the bus is read again
        self._store: Store[dict[str, object]] = Store(
            hass, _CACHE_STORAGE_VERSION, f"ebus_cache_{entry.entry_id}")

    async def _connect(self) -> tuple[SendFn, RecvFn]:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        self._writer = writer

        async def send(data: bytes) -> None:
            writer.write(data)
            await writer.drain()

        async def recv() -> bytes:
            return await reader.read(4096)

        return send, recv

    async def async_discover(self) -> None:
        """Connect, scan the bus, choose entities to expose and start the engine."""
        send, recv = await self._connect()
        scanner = ActiveReader(send, recv, qq=SOURCE_ADDRESS)
        for zz in SCAN_ADDRESSES:
            try:
                self.devices[zz] = await scanner.scan(zz, timeout=4.0)
            except Exception as err:  # noqa: BLE001 - a missing device is normal
                _LOGGER.debug("scan %#04x failed: %s", zz, err)
        # the broadcast circuit is not a scanned device: inject it so bus
        # broadcasts (outside temperature, date/time) become entities too
        self.devices[BROADCAST_ADDRESS] = ScanResult(
            mf=0, manufacturer="", ident=BROADCAST_IDENT,
            sw="", hw="", sw_num=None, hw_num=None)
        # register the adapter as the hub device: scanned devices hang off it
        # (via_device) and the broadcast entities belong to it
        registry = dr.async_get(self.hass)
        registry.async_get_or_create(
            config_entry_id=self.config_entry.entry_id,
            identifiers={adapter_identifier(self.host, self.port)},
            name="eBUS Adapter", manufacturer="eBUS", model="Enhanced adapter",
            sw_version=AIOEBUS_VERSION)
        # drop the former synthetic 'Broadcast' device: its entities now live on
        # the adapter, so it lingers as an empty shell from older versions
        stale = registry.async_get_device(
            identifiers={(DOMAIN, f"{BROADCAST_ADDRESS:02x}")})
        if stale is not None:
            registry.async_remove_device(stale.id)
        self.read_messages = device_messages(self.devices, "r", self.user_dir)
        self.write_messages = (
            device_messages(self.devices, "w", self.user_dir) if self.allow_write else [])

        registry = MessageRegistry(all_messages(self.devices, self.user_dir))
        self.engine = EbusEngine(send, recv, registry, scheduler=self._build_scheduler(),
                                 qq=SOURCE_ADDRESS, allow_write=self.allow_write)
        await self._warm_start_cache()
        # re-scope polling when the user enables/disables entities
        self.config_entry.async_on_unload(self.hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED, self._on_registry_update))
        self._supervisor = self.config_entry.async_create_background_task(
            self.hass, self._run_forever(), name="ebus-supervisor")
        _LOGGER.info("eBUS: %d device(s), %d read / %d write register(s)",
                     len(self.devices), len(self.read_messages), len(self.write_messages))

    def _build_scheduler(self) -> PollScheduler:
        """Poll only the read registers that back an enabled entity. Before any
        entity is registered (fresh install) poll every read, so values arrive
        and the entities can be created; after that, a disabled entity's register
        is dropped from the rotation (unless another enabled entity needs it)."""
        reg = er.async_get(self.hass)
        entries = er.async_entries_for_config_entry(reg, self.config_entry.entry_id)
        if not entries:
            polled: list[MessageDef] = [
                m for m in self.read_messages if m.direction[:1] == "r"]
        else:
            enabled = {e.unique_id for e in entries if e.disabled_by is None}
            polled = select_polled(
                self.read_messages, enabled,
                climate_zones(self.devices, self.user_dir),
                timer_schedules(self.devices, self.user_dir))
        return PollScheduler(polled, base_interval=self._base_interval,
                             default_priority=PLAIN_READ_PRIORITY)

    async def _on_registry_update(self, event: Event[Any]) -> None:
        """Rebuild the poll set when an entity is enabled or disabled."""
        if event.data.get("action") != "update":
            return
        if "disabled_by" not in event.data.get("changes", {}):
            return
        if self.engine is not None:
            self.engine.scheduler = self._build_scheduler()

    async def _run_forever(self) -> None:
        """Run the engine, reconnecting with backoff if the link drops."""
        assert self.engine is not None
        while True:
            try:
                await self.engine.run()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - keep the supervisor alive
                _LOGGER.debug("engine stopped: %s", err)
            if self._writer is not None:
                self._writer.close()
            await asyncio.sleep(_RECONNECT_BACKOFF)
            try:
                send, recv = await self._connect()
                self.engine.attach(send, recv)
                _LOGGER.info("eBUS: reconnected to %s:%s", self.host, self.port)
            except OSError as err:
                _LOGGER.warning("eBUS: reconnect failed: %s", err)

    async def _async_update_data(self) -> EbusCache:
        if self.engine is None:
            raise UpdateFailed("engine not started")
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
        for item in values:
            if (isinstance(item, list) and len(item) == 2
                    and isinstance(item[0], list) and len(item[0]) == 3):
                k = item[0]
                self.engine.cache[(str(k[0]), str(k[1]), str(k[2]))] = item[1]
        _LOGGER.debug("eBUS: warm-started %d cached value(s)", len(self.engine.cache))

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        if self.engine is not None:
            await self._store.async_save(self._serialize_cache())  # final flush
            self.engine.stop()
        if self._supervisor is not None:
            self._supervisor.cancel()
        if self._writer is not None:
            self._writer.close()
