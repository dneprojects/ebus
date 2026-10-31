"""The eBUS integration -- a thin Home Assistant front end for the aioebus
library (talks to an enhanced eBUS adapter directly, no ebusd, no MQTT)."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.storage import Store
from homeassistant.util import slugify

from .const import BROADCAST_ADDRESS, DOMAIN, STORE_PREFIXES
from .coordinator import EbusCoordinator
from .device import ADAPTER_IDENTIFIER
from .library import bundled_control_class, renamed_unique_id, retired_unique_id

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR, Platform.SENSOR, Platform.NUMBER,
    Platform.SELECT, Platform.CLIMATE]

type EbusConfigEntry = ConfigEntry[EbusCoordinator]


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


async def async_migrate_entry(hass: HomeAssistant, entry: EbusConfigEntry) -> bool:
    """Bring an entry from an older version up to date (runs once per step)."""
    if entry.version > 1:
        return False  # downgrade from a future version
    if entry.minor_version < 2:
        _migrate_retired_and_renamed(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=2)
    if entry.minor_version < 3:
        _migrate_nameless_entity_ids(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=3)
    if entry.minor_version < 4:
        _migrate_scoped_ids(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=4)
    if entry.minor_version < 5:
        await hass.async_add_executor_job(_load_definitions)
        _migrate_control_classes(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=5)
    return True


def _load_definitions() -> None:
    """Read the bundled definition files (blocking; run in an executor)."""
    bundled_control_class("")


def _migrate_control_classes(hass: HomeAssistant, entry: EbusConfigEntry) -> None:
    """1.4 -> 1.5: a writable register becomes a control only by its class
    (index.json 'controls'). Switch off configuration ones that used to be
    created enabled; the user can switch them on again. Controls a register no
    longer gets are dropped at setup, where the device's own definition is known."""
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if ent.domain not in ("number", "select") or not ent.unique_id.endswith("_set"):
            continue
        parts = ent.unique_id.split("_", 3)  # <entry>_<zz>_<message>_<label>_set
        cls = bundled_control_class(parts[2]) if len(parts) == 4 else None
        if cls == "installer" and ent.disabled_by is None:
            registry.async_update_entity(
                ent.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION)


_ADAPTER_UID = re.compile(r"^ebus_(?![0-9a-f]{2}_)(?P<host>.+)_(?P<port>\d+)_(?P<rest>.+)$")


def _migrate_scoped_ids(hass: HomeAssistant, entry: EbusConfigEntry) -> None:
    """1.3 -> 1.4: unique_ids start with the entry id instead of 'ebus' (two
    adapters with devices on the same addresses never collide), and the adapter's
    own entities and device are keyed independently of its address (a new IP
    keeps them). Entity ids, and so history, stay as they are."""
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        uid = ent.unique_id
        if (m := _ADAPTER_UID.match(uid)) is not None:
            new = f"{entry.entry_id}_adapter_{m['rest']}"
        elif uid.startswith("ebus_"):
            new = f"{entry.entry_id}_{uid[len('ebus_'):]}"
        else:
            continue
        if registry.async_get_entity_id(ent.domain, DOMAIN, new):
            registry.async_remove(ent.entity_id)
        else:
            registry.async_update_entity(ent.entity_id, new_unique_id=new)
    devices = dr.async_get(hass)
    old = (DOMAIN, f"{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}")
    adapter = devices.async_get_device_by_identifier(old, entry.entry_id)
    if adapter is not None and devices.async_get_device_by_identifier(
            ADAPTER_IDENTIFIER, entry.entry_id) is None:
        devices.async_update_device(adapter.id, new_identifiers={ADAPTER_IDENTIFIER})


def _migrate_nameless_entity_ids(hass: HomeAssistant, entry: EbusConfigEntry) -> None:
    """1.2 -> 1.3: an entity registered while its translation had no name (a 'uw'
    sensor before 0.3.1) got the bare device slug as entity_id; append its label."""
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    areas = ar.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        parts = ent.unique_id.split("_", 3)
        if len(parts) != 4 or parts[0] != "ebus" or ent.device_id is None:
            continue
        device = devices.async_get(ent.device_id)
        if device is None:
            continue
        # the id HA derived from the device name alone, possibly area-prefixed
        area = areas.async_get_area(device.area_id) if device.area_id else None
        names = {n for n in (device.name_by_user, device.name) if n}
        bare = {f"{ent.domain}.{slugify(n)}" for n in names}
        if area is not None:
            bare |= {f"{ent.domain}.{slugify(f'{area.name} {n}')}" for n in names}
        new_id = f"{ent.entity_id}_{slugify(parts[3])}"
        if ent.entity_id in bare and registry.async_get(new_id) is None:
            registry.async_update_entity(ent.entity_id, new_entity_id=new_id)


def _migrate_retired_and_renamed(hass: HomeAssistant, entry: EbusConfigEntry) -> None:
    """1.1 -> 1.2: drop the entities of retired messages and move renamed ones to
    their new unique_id, so they keep their entity_id and history. Where the
    successor already exists (created by 0.3.0), the old entry is the orphan."""
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if retired_unique_id(ent.unique_id):
            registry.async_remove(ent.entity_id)
            continue
        new_uid = renamed_unique_id(ent.unique_id)
        if new_uid is None:
            continue
        if registry.async_get_entity_id(ent.domain, DOMAIN, new_uid):
            registry.async_remove(ent.entity_id)
        else:
            registry.async_update_entity(ent.entity_id, new_unique_id=new_uid)


async def _async_reload(hass: HomeAssistant, entry: EbusConfigEntry) -> None:
    """Reload the entry when its options change (interval / write enable)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EbusConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: EbusConfigEntry) -> None:
    """Delete the entry's stored files (value cache, addresses, identifications)."""
    for prefix in STORE_PREFIXES:
        await Store(hass, 1, f"{prefix}{entry.entry_id}").async_remove()


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
    coordinator = entry.runtime_data
    kept = {ADAPTER_IDENTIFIER}
    kept |= {
        (DOMAIN, f"{zz:02x}")
        for zz in coordinator.devices
        if zz != BROADCAST_ADDRESS  # broadcasts belong to the adapter now
    }
    return not (device.identifiers & kept)
