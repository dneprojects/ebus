"""Loading the bundled eBUS device definitions and classifying their fields.

All protocol knowledge lives in aioebus; this module only turns the bundled
device configs into the lists the platforms need (which message/field becomes a
sensor, a number or a select) and small presentation helpers (icons).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from typing import Any, Final

from aioebus.conditions import context_from_scan, evaluate_all
from aioebus.convert import from_dicts
from aioebus.engine import value_key
from aioebus.messages import FieldDef, MessageDef
from aioebus.scan import ScanResult
from aioebus.translations import localized

_CONFIG_DIR: Final = Path(__file__).parent / "configs" / "vaillant"

# numeric field types that can be written as a Home Assistant number
NUMBER_TYPES: Final[frozenset[str]] = frozenset(
    {"UCH", "SCH", "UIN", "SIN", "ULG", "U2L", "D1B", "D1C", "D2B", "D2C", "FLT"})

# mdi icon by unit, else by a keyword in the field name (mirrors the eBUS Bridge)
_UNIT_ICON: Final[dict[str, str]] = {
    "°C": "mdi:thermometer", "bar": "mdi:gauge", "%": "mdi:percent",
    "kW": "mdi:lightning-bolt", "W": "mdi:lightning-bolt", "kWh": "mdi:counter",
    "l/h": "mdi:pump", "h": "mdi:clock-outline", "min": "mdi:clock-outline"}
_KEYWORD_ICON: Final[tuple[tuple[str, str], ...]] = (
    ("pump", "mdi:pump"), ("pressure", "mdi:gauge"),
    ("temp", "mdi:thermometer"), ("mode", "mdi:tune"),
    ("error", "mdi:alert-circle-outline"), ("time", "mdi:clock-outline"))

# Registry clean-up for config entry minor version 2 (run once by the entry
# migration). Retired: another register carries the same value (one home per
# value) -- the b51a lifetime yields and TotalEnergyUsage duplicate the b516/10
# float counters, CurrentConsumedPower and RunDataElectricPowerConsumption
# duplicate PowerConsumptionHmu. Renamed: the 0.2.0 names of the b516/10 counters,
# and SupplyTempWeighted, a second definition of TargetFlowTemp's register.
RETIRED_MESSAGES: Final[frozenset[str]] = frozenset({
    "YieldHc", "YieldHwc", "YieldCooling", "TotalEnergyUsage",
    "CurrentConsumedPower", "RunDataElectricPowerConsumption",
})
RENAMED_MESSAGES: Final[dict[str, str]] = {
    "EnergyEnvironmentHeat": "EnergyEnvironmentHc",
    "EnergyEnvironmentWater": "EnergyEnvironmentHwc",
    "EnergyYieldHeat": "EnergyGeneratedHc",
    "EnergyYieldWater": "EnergyGeneratedHwc",
    "EnergyYieldCool": "EnergyGeneratedCool",
    "EnergyElectricHeat": "EnergyElectricHc",
    "EnergyElectricWater": "EnergyElectricHwc",
    "SupplyTempWeighted": "TargetFlowTemp",
}


@dataclass
class Field:
    """One decodable field of a message, with its HA label and key."""
    msg: MessageDef
    field: FieldDef
    label: str  # HA-style label (decode_labeled key)


@cache
def _index() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((_CONFIG_DIR / "index.json").read_text(encoding="utf-8"))
    return data


@cache
def _load_file(name: str) -> tuple[MessageDef, ...]:
    return tuple(from_dicts(json.loads((_CONFIG_DIR / name).read_text(encoding="utf-8"))))


def _profile(ident: str, hw: str | None) -> dict[str, Any] | None:
    """The index entry for a scanned device by its ident and HW variant, as a
    profile {file, name?, model?, disabled_fields?}. An entry is a plain file name
    or such a profile, so the same device type behind a coupler can reuse one file
    under its own name. The address plays no role: identical devices share it."""
    entry = _index().get(ident)
    if isinstance(entry, dict) and "file" not in entry:
        entry = entry.get("hw", {}).get(hw or "") or entry.get("default")
    if isinstance(entry, str):
        return {"file": entry}
    if isinstance(entry, dict) and isinstance(entry.get("file"), str):
        return entry
    return None


def resolve_file(ident: str, hw: str | None) -> str | None:
    """Pick the definition file for a scanned device by its ident and HW variant."""
    profile = _profile(ident, hw)
    return profile["file"] if profile else None


def _profile_value(scan: ScanResult, key: str, table: str) -> Any:
    """A presentation value for a device: its profile's own entry wins, else the
    file-keyed table of the same meaning in index.json."""
    profile = _profile(scan.ident, scan.hw)
    if not profile:
        return None
    if key in profile:
        return profile[key]
    return _index().get(table, {}).get(profile["file"])


def disabled_field_labels(scan: ScanResult) -> set[str]:
    """Entity labels that should be created disabled by default for this device
    (unconnected channels, from 'disabled_fields' in index.json)."""
    # unlike name/model this adds up: the file's own unconnected channels plus the
    # ones a profile disables for its installation
    profile = _profile(scan.ident, scan.hw)
    if not profile:
        return set()
    out: set[str] = set()
    for labels in (_index().get("disabled_fields", {}).get(profile["file"]),
                   profile.get("disabled_fields")):
        if isinstance(labels, list):
            out.update(labels)
    return out


def device_name(scan: ScanResult) -> str:
    """Friendly device name for Home Assistant. Two devices can share a scan
    ident (e.g. an eloBlock and a heat pump both scan as 'V32') or a definition
    file (two heat pumps), so the profile/file name wins; else the scan ident."""
    name = _profile_value(scan, "name", "names")
    return name if isinstance(name, str) else scan.ident


def device_model(scan: ScanResult) -> str:
    """Model shown in HA. When a device sits behind a bus coupler the scan ident
    is the coupler (e.g. 'V32' for both the heat pump and the eloBlock); the
    profile or the file-keyed 'models' map names the actual appliance. Falls back
    to the scan ident."""
    model = _profile_value(scan, "model", "models")
    return model if isinstance(model, str) else scan.ident


def retired_unique_id(unique_id: str) -> bool:
    """True for a sensor unique_id ('ebus_<zz>_<message>_<label>') whose message
    was retired from the bundled definitions (see RETIRED_MESSAGES)."""
    parts = unique_id.split("_", 3)
    return len(parts) == 4 and parts[0] == "ebus" and parts[2] in RETIRED_MESSAGES


def renamed_unique_id(unique_id: str) -> str | None:
    """The current unique_id for one whose message was renamed (see
    RENAMED_MESSAGES), else None. The label starts with the message name."""
    parts = unique_id.split("_", 3)
    if len(parts) != 4 or parts[0] != "ebus" or parts[2] not in RENAMED_MESSAGES:
        return None
    old, new = parts[2], RENAMED_MESSAGES[parts[2]]
    label = new + parts[3][len(old):] if parts[3].startswith(old) else parts[3]
    return f"ebus_{parts[1]}_{new}_{label}"


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
        return list(_load_file(name))
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
    return tuple(from_dicts(json.loads(p.read_text(encoding="utf-8"))))


def preload(devices: dict[int, ScanResult], user_dir: Path | None = None) -> None:
    """Read every definition file the devices need (blocking I/O, so call it in an
    executor); afterwards the library works from memory only. User files are
    re-read on every setup, so a regenerated one counts after a reload."""
    _user_messages.cache_clear()
    _index()
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


def _decodable_fields(msg: MessageDef) -> list[FieldDef]:
    return [f for f in msg.fields if f.name and not f.type.upper().startswith("IGN")]


def _label(msg: MessageDef, field: FieldDef, fields: list[FieldDef]) -> str:
    lone_value = [f.name for f in fields] == ["value"]
    return msg.name if (field.name == "value" or lone_value) else f"{msg.name} {field.name}"


def fields_of(msg: MessageDef) -> list[Field]:
    fields = _decodable_fields(msg)
    return [Field(msg, f, _label(msg, f, fields)) for f in fields]


def label_for(field: Field, lang: str) -> str:
    """Localized display name for a field's entity, from the definition's own
    ``tr`` translations with fallback to the technical label. An entity named
    after the whole message (a lone 'value' field) takes the message translation;
    a multi-field entity takes the field translation. Until a definition carries
    ``tr`` (populated at import), this returns the technical label unchanged."""
    if field.label == field.msg.name:  # named after the message (lone value)
        return localized(field.msg.tr, lang, field.label)
    return localized(field.field.tr, lang, field.label)


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


@dataclass
class ClimateZone:
    """A heating zone to expose as one climate entity, with the addressed
    messages for its target setpoint (read/write), current temperature (read)
    and operating mode (read/write). Missing roles are None."""
    zz: str
    n: int
    name: str  # fallback name; the entity prefers the zone's own short name
    target_read: MessageDef | None
    target_write: MessageDef | None
    current: MessageDef | None
    mode_read: MessageDef | None
    mode_write: MessageDef | None
    shortname: MessageDef | None  # <prefix><n>Shortname, the user's zone label


def _find_msg(msgs: list[MessageDef], name: str, *, write: bool) -> MessageDef | None:
    for m in msgs:
        if m.name == name and (m.is_write if write else m.is_read):
            return m
    return None


def climate_zones(
    devices: dict[int, ScanResult], user_dir: Path | None = None
) -> list[ClimateZone]:
    """Bundle per-zone registers into climate zones, driven by the 'climate'
    section of index.json (keyed by the resolved definition file). For each zone
    N present, it gathers '<prefix>N<target/current/mode>' as the setpoint, room
    temperature and operating mode. Whether a zone is 'active' (has data) is left
    to the caller, which has the value cache."""
    decls = _index().get("climate", {})
    out: list[ClimateZone] = []
    for address, scan in devices.items():
        name = resolve_file(scan.ident, scan.hw)
        decl = decls.get(name) if name else None
        if not isinstance(decl, dict):
            continue
        prefix = str(decl.get("zone_prefix", ""))
        target, current, mode = decl["target"], decl["current"], decl["mode"]
        msgs = addressed_messages(address, scan, user_dir)
        rx = re.compile(rf"^{re.escape(prefix)}(\d+){re.escape(target)}$")
        nums = sorted({int(mt.group(1)) for m in msgs
                       if (mt := rx.match(m.name)) and m.is_write})
        for n in nums:
            out.append(ClimateZone(
                zz=f"{address:02x}", n=n,
                name=str(decl.get("name", "Zone {n}")).format(n=n),
                target_read=_find_msg(msgs, f"{prefix}{n}{target}", write=False),
                target_write=_find_msg(msgs, f"{prefix}{n}{target}", write=True),
                current=_find_msg(msgs, f"{prefix}{n}{current}", write=False),
                mode_read=_find_msg(msgs, f"{prefix}{n}{mode}", write=False),
                mode_write=_find_msg(msgs, f"{prefix}{n}{mode}", write=True),
                shortname=_find_msg(msgs, f"{prefix}{n}Shortname", write=False),
            ))
    return out


_WEEKDAY_NAMES: Final = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_WEEKDAY: Final = {name: i for i, name in enumerate(_WEEKDAY_NAMES)}
_TIMER_RE: Final = re.compile(
    r"^(?P<prefix>.+?)Timer_(?P<day>" + "|".join(_WEEKDAY) + r")(?P<slot>\d+)$")


@dataclass
class TimerSlot:
    """One weekly time-program slot message, with its weekday and slot index."""
    weekday: int
    slot: int
    msg: MessageDef


def timer_schedules(
    devices: dict[int, ScanResult], user_dir: Path | None = None
) -> dict[tuple[str, str], list[TimerSlot]]:
    """Group timer registers into weekly schedules per (device address, prefix).

    Convention-based, straight from the definition: a register named
    '<prefix>Timer_<Weekday><slot>' (e.g. HwcTimer_Monday0, Z1Timer_Tuesday1) is
    one slot of the '<prefix>' weekly program. No declaration needed -- the
    register naming in the def (which came from the CSV) is the declaration."""
    out: dict[tuple[str, str], list[TimerSlot]] = {}
    for address, scan in devices.items():
        for m in addressed_messages(address, scan, user_dir):
            mt = _TIMER_RE.match(m.name)
            if mt and m.is_read:
                key = (f"{address:02x}", mt.group("prefix"))
                out.setdefault(key, []).append(
                    TimerSlot(_WEEKDAY[mt.group("day")], int(mt.group("slot")), m))
    return out


def select_polled(
    read_messages: list[MessageDef],
    enabled_uids: set[str],
    zones: list[ClimateZone],
    schedules: dict[tuple[str, str], list[TimerSlot]],
    prefix: str,
) -> list[MessageDef]:
    """The read messages that back at least one *enabled* entity, so only those
    are polled. ``enabled_uids`` is the set of enabled ebus entity unique_ids,
    each starting with the entry's ``prefix``; a read backs a sensor (uid
    '<prefix>_<zz>_<name>_<label>'), a number/select (same + '_set'), a climate
    zone or a calendar. Keyed by (zz, name, label) which a read and its write
    twin share, so a writable's read twin is polled whenever its number/select
    is enabled."""
    polled: list[MessageDef] = []
    seen: set[int] = set()

    def add(msg: MessageDef | None) -> None:
        if msg is not None and id(msg) not in seen:
            seen.add(id(msg))
            polled.append(msg)

    for m in read_messages:
        if m.direction[:1] != "r":
            continue
        for field in fields_of(m):
            base = "_".join((prefix, *value_key(m, field.label)))
            if base in enabled_uids or f"{base}_set" in enabled_uids:
                add(m)
                break
    for zone in zones:
        if f"{prefix}_{zone.zz}_z{zone.n}_climate" in enabled_uids:
            for msg in (zone.target_read, zone.current, zone.mode_read, zone.shortname):
                add(msg)
    for (zz, tprefix), slots in schedules.items():
        if f"{prefix}_{f'{zz}_{tprefix}_timer'.lower()}" in enabled_uids:
            for slot in slots:
                add(slot.msg)
    return polled


def icon_for(field: FieldDef) -> str | None:
    if field.unit in _UNIT_ICON:
        return _UNIT_ICON[field.unit]
    name = field.name.lower()
    for keyword, icon in _KEYWORD_ICON:
        if keyword in name:
            return icon
    return None
