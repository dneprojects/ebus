"""Diagnostics for the eBUS integration.

Exposes the scanned bus layout and coordinator counters so a user can attach a
useful dump to a bug report. The config entry holds only a host and port (no
credentials or tokens), so host/port are shown as-is; the redaction hook is
wired through async_redact_data so any future sensitive key is covered."""
from __future__ import annotations

from typing import Any, Final

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import EbusConfigEntry

# No secrets exist in the entry today; keep the hook ready for future keys.
TO_REDACT: Final[set[str]] = set()


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EbusConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    devices = {
        f"{zz:#04x}": {
            "ident": scan.ident,
            "manufacturer": scan.manufacturer,
            "sw": scan.sw,
            "hw": scan.hw,
        }
        for zz, scan in coordinator.devices.items()
    }
    interval = coordinator.update_interval
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "allow_write": coordinator.allow_write,
        "update_interval_s": interval.total_seconds() if interval else None,
        "devices": devices,
        # telegrams (the devices' and ours) whose acknowledgment failed, the latest last
        "ack_faults": ([f.as_dict() for f in coordinator.engine.ack_faults]
                       if coordinator.engine else []),
        "counts": {
            "devices": len(coordinator.devices),
            "read_registers": len(coordinator.read_messages),
            "write_registers": len(coordinator.write_messages),
            "cached_values": len(coordinator.engine.cache) if coordinator.engine else 0,
            # times our polls gave way to a busy gateway
            "yields": coordinator.engine.stats.yields if coordinator.engine else 0,
        },
    }
