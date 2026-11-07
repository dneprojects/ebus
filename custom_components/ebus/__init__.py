"""The eBUS integration -- a thin Home Assistant front end for the aioebus
library (talks to an enhanced eBUS adapter directly, no ebusd, no MQTT)."""
from __future__ import annotations

import logging
import shutil
from functools import partial
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import DEFS_CACHE, DOMAIN, STORE_PREFIXES, STORE_VERSION
from .coordinator import EbusCoordinator
from .device import ADAPTER_IDENTIFIER
from .migrations import async_migrate_entry
from .services import async_setup_services
from .translate import forget_entry

__all__ = ["async_migrate_entry"]  # Home Assistant looks it up on this module

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR, Platform.SENSOR, Platform.NUMBER,
    Platform.SELECT, Platform.CLIMATE, Platform.WATER_HEATER]

type EbusConfigEntry = ConfigEntry[EbusCoordinator]


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EbusConfigEntry) -> bool:
    entry_ids = {e.entry_id for e in hass.config_entries.async_entries(DOMAIN)}
    if removed := await hass.async_add_executor_job(
            _remove_orphaned_stores, hass.config.path(".storage"), entry_ids):
        _LOGGER.debug("eBUS: removed stored files of deleted entries: %s", removed)
    coordinator = EbusCoordinator(hass, entry, entry.data[CONF_HOST], entry.data[CONF_PORT])
    await coordinator.async_discover()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # every entity is registered now: poll exactly what the enabled ones need
    # (entities new in this version included, without waiting for a restart)
    coordinator.async_rescope_polling()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: EbusConfigEntry) -> None:
    """Reload the entry when its address (the adapter moved to a new IP) or its
    options change; any other update (e.g. the entry keyed by the adapter's id)
    needs none."""
    coordinator = entry.runtime_data
    if dict(entry.data) != coordinator.entry_data or dict(entry.options) != coordinator.options:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EbusConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
        forget_entry(hass, entry.entry_id)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: EbusConfigEntry) -> None:
    """Delete the entry's stored files (value cache, addresses, identifications);
    with the last entry also the shared cache of downloaded definitions."""
    for prefix in STORE_PREFIXES:
        await Store(hass, STORE_VERSION, f"{prefix}{entry.entry_id}").async_remove()
    if not [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id]:
        await hass.async_add_executor_job(
            partial(shutil.rmtree, hass.config.path(".storage", DEFS_CACHE), ignore_errors=True))


def _remove_orphaned_stores(storage_dir: str, entry_ids: set[str]) -> list[str]:
    """Delete stored files of entries that no longer exist (removed before this
    clean-up existed). Blocking file I/O: run it in an executor."""
    removed: list[str] = []
    for path in Path(storage_dir).glob("ebus_*"):
        for prefix in STORE_PREFIXES:
            if not path.name.startswith(prefix):
                continue
            suffix = path.name[len(prefix):]
            # a dot marks HA's own copies (e.g. '<key>.corrupt.<time>' kept for a
            # repair issue): never ours to delete
            if "." in suffix or suffix in entry_ids:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:  # best effort: a file we cannot delete must not stop setup
                continue
            removed.append(path.name)
    return removed


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: EbusConfigEntry, device: DeviceEntry
) -> bool:
    """Let the user delete a stale device the current bus no longer backs.

    The adapter and every scanned device are kept; anything else (e.g. the former
    synthetic 'Broadcast' device, now folded into the adapter) may be removed."""
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None:  # not loaded (adapter offline): the bus cannot vouch
        return not (device.identifiers & {ADAPTER_IDENTIFIER})
    kept = {ADAPTER_IDENTIFIER}
    kept |= {(DOMAIN, f"{zz:02x}") for zz in coordinator.devices}
    return not (device.identifiers & kept)
