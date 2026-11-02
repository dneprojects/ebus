"""Constants for the eBUS integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "ebus"

CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_ALLOW_WRITE: Final = "allow_write"
CONF_CONFIG_DIR: Final = "config_dir"  # optional dir of user definitions (JSON or ebusd CSV)

DEFAULT_PORT: Final = 9999  # enhanced-adapter TCP port (enh:host:port)
DEFAULT_SCAN_INTERVAL: Final = 60  # base poll interval (seconds); priority scales it
# priority for plain `r` reads (no r1..r9 digit): polled every N * base interval,
# so ~5 min at the default. Kept rare because these are mostly static registers.
PLAIN_READ_PRIORITY: Final = 5
DEFAULT_ALLOW_WRITE: Final = False  # writing settings is opt-in

CONF_DEEP_SCAN: Final = "deep_scan"  # options: probe the full address space once
DEFAULT_DEEP_SCAN: Final = False

# per-entry stored files (value cache, extra addresses, identifications), each
# named <prefix><entry_id> under .storage
STORE_PREFIXES: Final[tuple[str, ...]] = ("ebus_cache_", "ebus_addrs_", "ebus_idents_")
CACHE_STORE, ADDRESS_STORE, IDENT_STORE = STORE_PREFIXES
STORE_VERSION: Final = 1
# shared cache of definitions downloaded from the CDN, under .storage
DEFS_CACHE: Final = "ebus_defs"
