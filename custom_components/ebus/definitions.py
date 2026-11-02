"""Which message definitions a scanned device has.

Resolves a scanned device to its bundled definition file (index.json) or a
user-generated one, and stamps its bus address onto the messages. Imports only
aioebus and the standard library, so tools and pure tests can load it without
Home Assistant.
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from functools import cache
from pathlib import Path
from typing import Any, Final

from aioebus.conditions import context_from_scan, evaluate_all
from aioebus.convert import from_dicts
from aioebus.messages import MessageDef
from aioebus.scan import ScanResult

_LOGGER = logging.getLogger(__name__)
CONFIG_DIR: Final = Path(__file__).parent / "configs" / "vaillant"


@cache
def index() -> dict[str, Any]:
    """The bundled index.json (device mapping and per-file presentation)."""
    data: dict[str, Any] = json.loads((CONFIG_DIR / "index.json").read_text(encoding="utf-8"))
    return data


@cache
def load_file(name: str) -> tuple[MessageDef, ...]:
    return tuple(from_dicts(json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))))


def profile(ident: str, hw: str | None) -> dict[str, Any] | None:
    """The index entry for a scanned device by its ident and HW variant, as a
    profile {file, name?, model?, disabled_fields?}. An entry is a plain file name
    or such a profile, so the same device type behind a coupler can reuse one file
    under its own name. The address plays no role: identical devices share it."""
    entry = index().get(ident)
    if isinstance(entry, dict) and "file" not in entry:
        entry = entry.get("hw", {}).get(hw or "") or entry.get("default")
    if isinstance(entry, str):
        return {"file": entry}
    if isinstance(entry, dict) and isinstance(entry.get("file"), str):
        return entry
    return None


def resolve_file(ident: str, hw: str | None) -> str | None:
    """Pick the definition file for a scanned device by its ident and HW variant."""
    found = profile(ident, hw)
    return found["file"] if found else None


def file_section(scan: ScanResult | None, section: str) -> Any:
    """A device's entry in a file-keyed section of index.json (controls,
    number_bounds, climate, ...), or None."""
    found = profile(scan.ident, scan.hw) if scan is not None else None
    return index().get(section, {}).get(found["file"]) if found else None


def profile_value(scan: ScanResult, key: str, table: str) -> Any:
    """A presentation value for a device: its profile's own entry wins, else the
    file-keyed table of the same meaning in index.json."""
    found = profile(scan.ident, scan.hw)
    if not found:
        return None
    if key in found:
        return found[key]
    return index().get(table, {}).get(found["file"])


def cdn_paths(devices: dict[int, ScanResult]) -> list[str]:
    """The upstream CDN files that carry display text for these devices."""
    cdn = index().get("cdn", {})
    paths: set[str] = set()
    for scan in devices.values():
        file = resolve_file(scan.ident, scan.hw)
        if file:
            paths.update(cdn.get(file, []))
    return sorted(paths)


def resolve_user_file(scan: ScanResult, user_dir: Path) -> Path | None:
    """Find a user-generated definition file for a scanned device, matching the
    scan ident (longest filename ident that is a prefix of the lowercased scan
    id) and preferring the matching HW variant. Lets a user cover any device by
    converting their own ebusd-configuration locally (see docs/local-configs.md)."""
    ident = (scan.ident or "").lower()
    if not ident:
        return None
    cands: list[tuple[int, str, Path]] = []
    for p in sorted(user_dir.glob("*.json")):
        parts = p.stem.split(".")  # ZZ . ident [. variant]
        if len(parts) < 2:
            continue
        fid = parts[1].lower()
        if fid and ident.startswith(fid):
            cands.append((len(fid), parts[2] if len(parts) > 2 else "", p))
    if not cands:
        return None
    best = max(c[0] for c in cands)
    matches = [(variant, path) for length, variant, path in cands if length == best]
    want_hw = f"HW{scan.hw or ''}".upper()
    for variant, path in matches:  # prefer HW variant, then the plain file, then any
        if variant.upper() == want_hw:
            return path
    for variant, path in matches:
        if variant == "":
            return path
    return matches[0][1]


def _messages_for(scan: ScanResult, user_dir: Path | None) -> list[MessageDef]:
    """Our own bundled definition wins; otherwise a user-generated one."""
    name = resolve_file(scan.ident, scan.hw)
    if name is not None:
        return list(load_file(name))
    if user_dir is not None:
        return list(_user_messages(scan.ident, scan.hw, str(user_dir)))
    return []


@cache
def _user_messages(ident: str, hw: str, user_dir: str) -> tuple[MessageDef, ...]:
    """A device's user-generated definition, read once per run (file I/O: warm it
    in an executor, see preload)."""
    scan = ScanResult(0, "", ident, "", hw, None, None)
    p = resolve_user_file(scan, Path(user_dir))
    if p is None:
        return ()
    try:
        return tuple(from_dicts(json.loads(p.read_text(encoding="utf-8"))))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as err:
        # one broken file costs its device, never the whole entry
        _LOGGER.warning("eBUS: definition file %s skipped: %s", p.name, err)
        return ()


def preload(devices: dict[int, ScanResult], user_dir: Path | None = None) -> None:
    """Read every definition file the devices need (blocking I/O, so call it in an
    executor); afterwards the definitions come from memory only. User files are
    re-read on every setup, so a regenerated one counts after a reload."""
    _user_messages.cache_clear()
    index()
    all_messages(devices, user_dir)


def addressed_messages(address: int, scan: ScanResult,
                       user_dir: Path | None = None) -> list[MessageDef]:
    """The messages for one scanned device, with its bus address stamped on.

    Conditions are evaluated against *this device's* scan (id_sw / id_hw):
    messages whose condition is False for the device are dropped, and where
    several definitions share a command (pbsb+id) the one that best fits the
    device wins (a matched condition over an unconditional base over an
    unresolved one). Two devices of the same type still share one file."""
    zz = f"{address:02x}"
    ctx = context_from_scan(scan)
    chosen: dict[tuple[str, str], tuple[int, MessageDef]] = {}
    for m in _messages_for(scan, user_dir):
        result = evaluate_all(m.conditions, ctx)
        if result is False:
            continue
        # matched variant (3) beats unconditional base (2) beats unresolved (1)
        rank = (3 if result is True else 1) if m.conditions else 2
        key = (m.pbsb, m.msgid)
        if key not in chosen or rank > chosen[key][0]:
            chosen[key] = (rank, m)
    return [replace(m, zz=zz) for _, m in chosen.values()]


def all_messages(devices: dict[int, ScanResult],
                 user_dir: Path | None = None) -> list[MessageDef]:
    """Addressed messages across every scanned device (for the passive registry)."""
    out: list[MessageDef] = []
    for address, scan in devices.items():
        out += addressed_messages(address, scan, user_dir)
    return out


def device_messages(devices: dict[int, ScanResult], direction: str,
                    user_dir: Path | None = None) -> list[MessageDef]:
    """Addressed messages of a given direction ('r' read / 'w' write) across all
    scanned devices, de-duplicated per (address, name)."""
    want_read = direction == "r"
    out: list[MessageDef] = []
    seen: set[tuple[str, str]] = set()
    for m in all_messages(devices, user_dir):
        # reads include passive updates (u), so broadcast values surface as
        # sensors; only true writes (w) go to the number/select platforms
        if want_read != m.is_read or (not want_read and not m.is_write):
            continue
        key = (m.zz, m.name)
        if key in seen:  # first definition wins (avoids duplicate entities)
            continue
        seen.add(key)
        out.append(m)
    return out
