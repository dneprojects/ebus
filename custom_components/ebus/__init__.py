"""The eBUS integration -- a thin Home Assistant front end for the aioebus
library (talks to an enhanced eBUS adapter directly, no ebusd, no MQTT)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry

from .const import BROADCAST_ADDRESS, DOMAIN
from .coordinator import EbusCoordinator
from .device import adapter_identifier
from .library import retired_unique_id

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR, Platform.SENSOR, Platform.NUMBER,
    Platform.SELECT, Platform.CLIMATE]

type EbusConfigEntry = ConfigEntry[EbusCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: EbusConfigEntry) -> bool:
    coordinator = EbusCoordinator(hass, entry, entry.data[CONF_HOST], entry.data[CONF_PORT])
    await coordinator.async_discover()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    _remove_retired_entities(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


def _remove_retired_entities(hass: HomeAssistant, entry: EbusConfigEntry) -> None:
    """Drop registry entries of messages retired from the bundled definitions, so
    they do not linger as orphaned 'unavailable' entities."""
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if retired_unique_id(ent.unique_id):
            registry.async_remove(ent.entity_id)


async def _async_reload(hass: HomeAssistant, entry: EbusConfigEntry) -> None:
    """Reload the entry when its options change (interval / write enable)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EbusConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: EbusConfigEntry, device: DeviceEntry
) -> bool:
    """Let the user delete a stale device the current bus no longer backs.

    The adapter and every scanned device are kept; anything else (e.g. the former
    synthetic 'Broadcast' device, now folded into the adapter) may be removed."""
    coordinator = entry.runtime_data
    kept = {adapter_identifier(coordinator.host, coordinator.port)}
    kept |= {
        (DOMAIN, f"{zz:02x}")
        for zz in coordinator.devices
        if zz != BROADCAST_ADDRESS  # broadcasts belong to the adapter now
    }
    return not (device.identifiers & kept)
