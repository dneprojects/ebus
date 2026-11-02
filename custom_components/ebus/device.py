"""Shared Home Assistant device info for the eBUS adapter and scanned devices."""
from __future__ import annotations

from typing import Any

from aioebus import __version__ as AIOEBUS_VERSION
from aioebus.catalog import Definition
from aioebus.scan import ScanResult
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .adapter import wifi
from .const import BROADCAST_ADDRESS, DOMAIN
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


def adapter_registry_info(entry_id: str, host: str, status: dict[str, Any]) -> dict[str, Any]:
    """Device registry fields of the adapter hub, enriched from its web status."""
    info: dict[str, Any] = {
        "config_entry_id": entry_id,
        "identifiers": {ADAPTER_IDENTIFIER},
        "name": ADAPTER_NAME,
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
    if zz == BROADCAST_ADDRESS:
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
