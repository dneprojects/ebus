"""Replay a captured telegram log through the integration pipeline.

A regression check for the bundled device definitions: it reproduces exactly
what the coordinator builds at runtime -- scan -> ident resolution -> message
set -> passive decode -> entity value cache -- but fed from the recorded `raw`
wire bytes of an ebusd SQLite capture instead of a live adapter. No bus or
adapter is touched, so it runs while ebusd keeps the adapter.

    python tools/replay.py <telegrams.db> [--scan 08,0a,15,...] [--values]

The scan addresses default to a typical Vaillant bus; device idents and SW/HW
are read from the capture's own 0704 answers, so the result reflects whatever
installation produced the log. --values also prints a sample of decoded values.
"""
# ruff: noqa: I001 -- pure_loader must come before the aioebus imports
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pure_loader import pure  # first: it finds aioebus (a sibling checkout if not installed)

from aioebus.discovery import BASE_ADDRESSES, BROADCAST_ADDRESS
from aioebus.passive import MessageRegistry, decode_raw
from aioebus.scan import ScanResult, decode_scan

definitions = pure("definitions")
entities = pure("entities")

def _scan_devices(db: sqlite3.Connection, addrs: tuple[int, ...]) -> dict[int, ScanResult]:
    """Resolve each address from its latest 0704 answer in the capture."""
    devices: dict[int, ScanResult] = {}
    for zz in addrs:
        row = db.execute(
            "SELECT s_dd FROM telegram WHERE status='ok' AND zz=? AND pb=7 AND sb=4 "
            "AND s_dd!='' ORDER BY ts DESC LIMIT 1", (zz,)).fetchone()
        if row:
            devices[zz] = decode_scan(bytes.fromhex(row[0]))
    devices[BROADCAST_ADDRESS] = ScanResult(0, "", "Broadcast", "", "", None, None)
    return devices


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Replay a telegram log through the integration.")
    ap.add_argument("db", type=Path, help="path to an ebusd telegrams.db capture")
    ap.add_argument("--scan", help="comma-separated hex addresses to scan (default: Vaillant set)")
    ap.add_argument("--values", action="store_true", help="also print a sample of decoded values")
    args = ap.parse_args(argv)

    addrs = (tuple(int(x, 16) for x in args.scan.split(",")) if args.scan else BASE_ADDRESSES)
    db = sqlite3.connect(args.db)

    devices = _scan_devices(db, addrs)
    msgs = definitions.all_messages(devices)
    registry = MessageRegistry(msgs)
    reads = definitions.device_messages(devices, "r")
    writes = [m for m in definitions.device_messages(devices, "w") if entities.control_of(m)]

    cache: dict[tuple[str, str, str], object] = {}
    hits = 0
    for (raw,) in db.execute("SELECT raw FROM telegram WHERE status='ok'"):
        res = decode_raw(registry, raw)
        if res is None:
            continue
        msg, values = res
        hits += 1
        for field, val in values.items():
            if val is not None:
                cache[(msg.zz, msg.name, field)] = val

    print(f"devices scanned: {len(devices)}  message defs: {len(msgs)}")
    print(f"messages: {len(reads)} read, {len(writes)} with a control")
    print(f"telegrams decoded: {hits}; distinct values cached: {len(cache)}\n")

    by_zz: dict[str, int] = defaultdict(int)
    for (zz, _, _) in cache:
        by_zz[zz] += 1
    for zz, scan in sorted(devices.items()):
        key = f"{zz:02x}"
        print(f"  {key} {scan.ident:10} {by_zz.get(key, 0):4} live values")

    read_keys = {(m.zz, m.name) for m in reads}
    cached_names = {(zz, nm) for (zz, nm, _) in cache}
    missing = sorted(k for k in read_keys if k not in cached_names)
    print(f"\nread entities with a value: {len(read_keys) - len(missing)}/{len(read_keys)}")
    for zz, nm in missing[:20]:
        print(f"    no value: {zz} {nm}")

    if args.values:
        print("\ndecoded values:")
        for (zz, name, field), val in sorted(cache.items()):
            print(f"  {zz} {name} [{field}] = {val}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
