"""The Home Assistant devices: the adapter hub and the scanned bus devices --
their device info and the registry housekeeping around them."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from aioebus import __version__ as AIOEBUS_VERSION
from aioebus.catalog import Definition
from aioebus.frame import BROADCAST
from aioebus.scan import ScanResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .adapter import wifi
from .const import DOMAIN
from .entities import device_model, device_name

# device identifiers are unique within a config entry, so the adapter of each
# entry is simply "adapter" -- independent of its IP address
ADAPTER_IDENTIFIER: tuple[str, str] = (DOMAIN, "adapter")
ADAPTER_NAME = "eBUS Adapter"


def adapter_device_info() -> DeviceInfo:
    """The adapter itself, as the hub device every scanned device hangs off and
    the bus-wide broadcasts (outside temperature, date/time) belong to.

    Deliberately minimal (identifiers + name): the coordinator registers the same
    device with the rich fields (firmware, hardware, web-UI link) from the adapter
    status, and entities must not overwrite those with placeholders here."""
    return DeviceInfo(identifiers={ADAPTER_IDENTIFIER}, name=ADAPTER_NAME)


def adapter_registry_info(entry_id: str, host: str, status: dict[str, Any],
                          *, known: bool = False) -> dict[str, Any]:
    """Device registry fields of the adapter hub, enriched from its web status.
    Without a status for an adapter ``known`` from before (its web API did not
    answer this time), the fields it had are left as they are."""
    info: dict[str, Any] = {
        "config_entry_id": entry_id,
        "identifiers": {ADAPTER_IDENTIFIER},
        "name": ADAPTER_NAME,
    }
    if known and not status:
        return info
    info |= {
        "manufacturer": "eBUS",
        "model": (f"eBUS Adapter Shield ({status['chip']})"
                  if status.get("chip") else "Enhanced adapter"),
        # the adapter firmware is what the user means by "firmware"; fall back
        # to the library version when there is no web API to ask
        "sw_version": status.get("versions") or AIOEBUS_VERSION,
    }
    if status.get("pcb"):
        info["hw_version"] = status["pcb"]
    if status.get("id"):
        info["serial_number"] = status["id"]
    mac = wifi(status).get("mac")
    if isinstance(mac, str) and mac:
        info["connections"] = {(dr.CONNECTION_NETWORK_MAC, mac)}
    if status:  # only link a web UI we actually reached
        info["configuration_url"] = f"http://{host}/"
    return info


def device_info(zz: int, scan: ScanResult | None, definition: Definition | None,
                adapter_device_id: str | None) -> DeviceInfo:
    # the broadcast pseudo-address is not a real device -- its entities belong to
    # the adapter (the bus itself); everything else hangs off the adapter
    if zz == BROADCAST:
        return adapter_device_info()
    info = DeviceInfo(
        identifiers={(DOMAIN, f"{zz:02x}")},
        name=device_name(scan, definition) if scan else f"eBUS {zz:#04x}",
        manufacturer=scan.manufacturer if scan else None,
        # the appliance, not the coupler ident
        model=device_model(scan, definition) if scan else None,
        sw_version=scan.sw if scan else None,
        hw_version=scan.hw if scan else None,
    )
    if adapter_device_id is not None:
        info["via_device_id"] = adapter_device_id
    return info


def register_adapter(hass: HomeAssistant, entry_id: str, host: str,
                     status: dict[str, Any]) -> str:
    """Create or update the adapter hub device; its registry id."""
    registry = dr.async_get(hass)
    known = registry.async_get_device_by_identifier(ADAPTER_IDENTIFIER, entry_id) is not None
    return registry.async_get_or_create(
        **adapter_registry_info(entry_id, host, status, known=known)).id


def remove_devices(hass: HomeAssistant, entry_id: str, addresses: Iterable[int]) -> None:
    """Remove the devices of these bus addresses (phantom addresses of a unit found
    elsewhere, and the former synthetic 'Broadcast' device, whose entities live on
    the adapter now)."""
    registry = dr.async_get(hass)
    for zz in addresses:
        dev = registry.async_get_device_by_identifier((DOMAIN, f"{zz:02x}"), entry_id)
        if dev is not None:
            registry.async_remove_device(dev.id)


def adopt_adapter_id(hass: HomeAssistant, entry: ConfigEntry, adapter_id: object) -> None:
    """Key the entry by the adapter's own id (its MAC) once it is known, so a new IP
    address is recognised as the same adapter instead of a new one."""
    if not isinstance(adapter_id, str) or not adapter_id or entry.unique_id == adapter_id:
        return
    if any(e.unique_id == adapter_id
           for e in hass.config_entries.async_entries(DOMAIN) if e is not entry):
        return  # another entry already claims it; leave both untouched
    hass.config_entries.async_update_entry(entry, unique_id=adapter_id)
