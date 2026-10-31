"""Constants for the eBUS integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "ebus"

CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_ALLOW_WRITE: Final = "allow_write"
CONF_CONFIG_DIR: Final = "config_dir"  # optional dir of user-generated device JSONs

DEFAULT_PORT: Final = 9999  # enhanced-adapter TCP port (enh:host:port)
DEFAULT_SCAN_INTERVAL: Final = 60  # base poll interval (seconds); priority scales it
# priority for plain `r` reads (no r1..r9 digit): polled every N * base interval,
# so ~5 min at the default. Kept rare because these are mostly static registers.
PLAIN_READ_PRIORITY: Final = 5
DEFAULT_ALLOW_WRITE: Final = False  # writing settings is opt-in

# device addresses to scan at setup (eBUS slave addresses seen on a Vaillant bus)
SCAN_ADDRESSES: Final[tuple[int, ...]] = (0x08, 0x0A, 0x15, 0x18, 0x26, 0x38, 0x76, 0xF6)

# a full ("deep") scan probes the whole eBUS device-address space: the high/low
# nibble families where eBUS slaves live (covers all of SCAN_ADDRESSES and the
# usual Vaillant addresses). Found devices are cached so later setups stay fast.
SCAN_ALL_ADDRESSES: Final[tuple[int, ...]] = tuple(
    (hi << 4) | lo
    for hi in (0x0, 0x1, 0x2, 0x3, 0x5, 0x7, 0xF)
    for lo in (0x3, 0x4, 0x5, 0x6, 0x8, 0xA, 0xC)
)
CONF_DEEP_SCAN: Final = "deep_scan"  # options: probe the full address space once
DEFAULT_DEEP_SCAN: Final = False

# master address aioebus uses on the bus; must be free (ebusd's own address, so
# this integration replaces ebusd rather than running alongside it)
SOURCE_ADDRESS: Final = 0x31

# synthetic address for the broadcast circuit (telegrams sent to 0xfe); not a
# scanned device, injected by the coordinator so bus broadcasts become entities
BROADCAST_ADDRESS: Final = 0xFE
BROADCAST_IDENT: Final = "Broadcast"

# per-entry stored files (value cache, extra addresses, identifications), each
# named <prefix><entry_id> under .storage
STORE_PREFIXES: Final[tuple[str, ...]] = ("ebus_cache_", "ebus_addrs_", "ebus_idents_")
