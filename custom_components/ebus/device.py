"""Shared Home Assistant device info for the eBUS adapter and scanned devices."""
from __future__ import annotations

from aioebus.scan import ScanResult
from homeassistant.helpers.device_registry import DeviceInfo

from .const import BROADCAST_ADDRESS, DOMAIN
from .library import device_model, device_name


def adapter_identifier(host: str, port: int) -> tuple[str, str]:
    return (DOMAIN, f"{host}:{port}")


def adapter_device_info(host: str, port: int) -> DeviceInfo:
    """The adapter itself, as the hub device every scanned device hangs off and
    the bus-wide broadcasts (outside temperature, date/time) belong to.

    Deliberately minimal (identifiers + name): the coordinator registers the same
    device with the rich fields (firmware, hardware, web-UI link) from the adapter
    status, and entities must not overwrite those with placeholders here."""
    return DeviceInfo(
        identifiers={adapter_identifier(host, port)},
        name="eBUS Adapter",
    )


def device_info(zz: int, scan: ScanResult | None, host: str, port: int) -> DeviceInfo:
    # the broadcast pseudo-address is not a real device -- its entities belong to
    # the adapter (the bus itself); everything else hangs off the adapter
    if zz == BROADCAST_ADDRESS:
        return adapter_device_info(host, port)
    return DeviceInfo(
        identifiers={(DOMAIN, f"{zz:02x}")},
        name=device_name(scan) if scan else f"eBUS {zz:#04x}",
        manufacturer=scan.manufacturer if scan else None,
        model=device_model(scan) if scan else None,  # appliance, not the coupler ident
        sw_version=scan.sw if scan else None,
        hw_version=scan.hw if scan else None,
        via_device=adapter_identifier(host, port),
    )
