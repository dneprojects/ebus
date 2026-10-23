"""The eBUS integration -- a thin Home Assistant front end for the aioebus
library (talks to an enhanced eBUS adapter directly, no ebusd, no MQTT)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .coordinator import EbusCoordinator

PLATFORMS: list[Platform] = [
    Platform.SENSOR, Platform.NUMBER, Platform.SELECT, Platform.CLIMATE]

type EbusConfigEntry = ConfigEntry[EbusCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: EbusConfigEntry) -> bool:
    coordinator = EbusCoordinator(hass, entry, entry.data[CONF_HOST], entry.data[CONF_PORT])
    await coordinator.async_discover()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: EbusConfigEntry) -> None:
    """Reload the entry when its options change (interval / write enable)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EbusConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
