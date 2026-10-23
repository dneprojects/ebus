"""Shared Home Assistant device info for a scanned eBUS device."""
from __future__ import annotations

from aioebus.scan import ScanResult
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def device_info(zz: int, scan: ScanResult | None) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{zz:02x}")},
        name=scan.ident if scan else f"eBUS {zz:#04x}",
        manufacturer=scan.manufacturer if scan else None,
        model=scan.ident if scan else None,
        sw_version=scan.sw if scan else None,
        hw_version=scan.hw if scan else None,
    )
