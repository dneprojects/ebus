"""Shared Home Assistant device info for a scanned eBUS device."""
from __future__ import annotations

from aioebus.scan import ScanResult
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .library import device_name


def device_info(zz: int, scan: ScanResult | None) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{zz:02x}")},
        name=device_name(scan) if scan else f"eBUS {zz:#04x}",
        manufacturer=scan.manufacturer if scan else None,
        model=scan.ident if scan else None,  # keep the raw scan ident as the model
        sw_version=scan.sw if scan else None,
        hw_version=scan.hw if scan else None,
    )
