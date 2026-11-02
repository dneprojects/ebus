"""What Home Assistant entities a device's messages become.

One home for the decisions the platforms, the polling scope and the translation
generator share: which field is a sensor, a number or a select, a writable
register's control class and range, a device's name, icons, climate zones.
Imports only aioebus, the standard library and other pure modules of this
package, so tools and pure tests can load it without Home Assistant.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from aioebus.codecs import is_numeric
from aioebus.messages import FieldDef, MessageDef
from aioebus.scan import ScanResult

from .definitions import (
    addressed_messages,
    file_section,
    index,
    load_file,
    profile,
    profile_value,
    resolve_file,
)
from .ids import control_uid, entity_uid, zone_uid

FLOAT_TYPES: Final[frozenset[str]] = frozenset({"EXP", "EXR"})  # numbers need explicit bounds

# mdi icon by unit, else by a keyword in the field name (mirrors the eBUS Bridge)
_UNIT_ICON: Final[dict[str, str]] = {
    "°C": "mdi:thermometer", "bar": "mdi:gauge", "%": "mdi:percent",
    "kW": "mdi:lightning-bolt", "W": "mdi:lightning-bolt", "kWh": "mdi:counter",
    "l/h": "mdi:pump", "h": "mdi:clock-outline", "min": "mdi:clock-outline"}
_KEYWORD_ICON: Final[tuple[tuple[str, str], ...]] = (
    ("pump", "mdi:pump"), ("pressure", "mdi:gauge"),
    ("temp", "mdi:thermometer"), ("mode", "mdi:tune"),
    ("error", "mdi:alert-circle-outline"), ("time", "mdi:clock-outline"))

# number range by unit for float registers (they have no natural raw range)
_UNIT_BOUNDS: Final[dict[str, tuple[float, float, float]]] = {
    "°C": (0.0, 90.0, 0.5), "K": (0.0, 30.0, 0.5), "h": (0.0, 24.0, 0.5), "": (0.0, 10.0, 0.05)}


@dataclass
class Field:
    """One decodable field of a message, with its HA label and key."""
    msg: MessageDef
    field: FieldDef
    label: str  # HA-style label (decode_labeled key)


def fields_of(msg: MessageDef) -> list[Field]:
    """One entry per entity the message's values give."""
    fields = msg.value_fields
    lone_value = [f.name for f in fields] == ["value"]
    return [Field(msg, f, msg.name if (f.name == "value" or lone_value)
                  else f"{msg.name} {f.name}") for f in fields]


def _base(type_: str) -> str:
    return type_.upper().split(":", 1)[0]


def is_number_type(type_: str) -> bool:
    """A type a number control can write (integers, and floats with bounds)."""
    return is_numeric(type_) or _base(type_) in FLOAT_TYPES


def has_numeric_value(type_: str) -> bool:
    """A type that decodes to a number, so its sensor carries statistics."""
    return is_number_type(type_) or _base(type_) == "BCD"


def control_of(msg: MessageDef) -> tuple[Literal["number", "select"], Field] | None:
    """The control a write message becomes: a select for a value table, a number
    for a numeric type; only single-field writes get one."""
    if not msg.is_write:
        return None
    fields = fields_of(msg)
    if len(fields) != 1:
        return None
    field = fields[0]
    if field.field.values:
        return "select", field
    if is_number_type(field.field.type):
        return "number", field
    return None


def control_class(scan: ScanResult | None, message: str) -> str:
    """What a writable register becomes: "user" (a control, enabled), "installer"
    (a configuration control, created disabled) or "none" (no control, the
    sensor only), from the 'controls' section of index.json. A bundled file
    lists its user and installer registers, so anything unlisted (a counter, a
    live state) gets no control; a device without a section (a user-generated
    definition) gets configuration controls the user enables deliberately."""
    section = file_section(scan, "controls")
    if not isinstance(section, dict):
        return "installer"
    for cls in ("user", "installer"):
        if message in section.get(cls, ()):
            return cls
    return "none"


def bundled_control_class(message: str) -> str | None:
    """The class of a register name across the bundled files with a 'controls'
    section (for migrating entities registered before the classes existed):
    the most permissive class it has anywhere, "none" if those files define it
    without listing it, None if no such file defines it (a user definition)."""
    classes: set[str] = set()
    for file, section in index().get("controls", {}).items():
        if file.startswith("_") or not isinstance(section, dict):
            continue
        if message not in {m.name for m in load_file(file)}:
            continue
        classes.add(next((c for c in ("user", "installer") if message in section.get(c, ())),
                         "none"))
    for cls in ("user", "installer", "none"):
        if cls in classes:
            return cls
    return None


def number_bounds(scan: ScanResult | None, message: str, field: FieldDef
                  ) -> tuple[float, float, float] | None:
    """(min, max, step) for a number from index.json 'number_bounds', else a
    unit default for a float register; None leaves an integer type's range."""
    if (bounds := listed_bounds(scan, message)) is not None:
        return bounds
    if _base(field.type) in FLOAT_TYPES:
        return _UNIT_BOUNDS.get(field.unit or "", _UNIT_BOUNDS[""])
    return None


def listed_bounds(scan: ScanResult | None, message: str) -> tuple[float, float, float] | None:
    """(min, max, step) of a register as index.json 'number_bounds' lists it."""
    table = file_section(scan, "number_bounds")
    bounds = table.get(message) if isinstance(table, dict) else None
    if isinstance(bounds, list) and len(bounds) == 3:
        return (float(bounds[0]), float(bounds[1]), float(bounds[2]))
    return None


def disabled_field_labels(scan: ScanResult) -> set[str]:
    """Entity labels that should be created disabled by default for this device
    (unconnected channels, from 'disabled_fields' in index.json)."""
    # unlike name/model this adds up: the file's own unconnected channels plus the
    # ones a profile disables for its installation
    found = profile(scan.ident, scan.hw)
    if not found:
        return set()
    out: set[str] = set()
    for labels in (index().get("disabled_fields", {}).get(found["file"]),
                   found.get("disabled_fields")):
        if isinstance(labels, list):
            out.update(labels)
    return out


def device_name(scan: ScanResult) -> str:
    """Friendly device name for Home Assistant. Two devices can share a scan
    ident (e.g. an eloBlock and a heat pump both scan as 'V32') or a definition
    file (two heat pumps), so the profile/file name wins; else the scan ident."""
    name = profile_value(scan, "name", "names")
    return name if isinstance(name, str) else scan.ident


def device_model(scan: ScanResult) -> str:
    """Model shown in HA. When a device sits behind a bus coupler the scan ident
    is the coupler (e.g. 'V32' for both the heat pump and the eloBlock); the
    profile or the file-keyed 'models' map names the actual appliance. Falls back
    to the scan ident."""
    model = profile_value(scan, "model", "models")
    return model if isinstance(model, str) else scan.ident


def icon_for(field: FieldDef) -> str | None:
    if field.unit in _UNIT_ICON:
        return _UNIT_ICON[field.unit]
    name = field.name.lower()
    for keyword, icon in _KEYWORD_ICON:
        if keyword in name:
            return icon
    return None


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
    # (min, max, step) of the setpoint, from the target register's number bounds
    target_bounds: tuple[float, float, float] | None = None


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
    out: list[ClimateZone] = []
    for address, scan in devices.items():
        decl = file_section(scan, "climate") if resolve_file(scan.ident, scan.hw) else None
        if not isinstance(decl, dict):
            continue
        prefix = str(decl.get("zone_prefix", ""))
        target, current, mode = decl["target"], decl["current"], decl["mode"]
        msgs = addressed_messages(address, scan, user_dir)
        rx = re.compile(rf"^{re.escape(prefix)}(\d+){re.escape(target)}$")
        nums = sorted({int(mt.group(1)) for m in msgs
                       if (mt := rx.match(m.name)) and m.is_write})
        for n in nums:
            target_write = _find_msg(msgs, f"{prefix}{n}{target}", write=True)
            # only a listed range: a unit default (0..90 for any temperature) would
            # be no better than the thermostat's own 5..30
            bounds = listed_bounds(scan, target_write.name) if target_write else None
            out.append(ClimateZone(
                zz=f"{address:02x}", n=n,
                name=str(decl.get("name", "Zone {n}")).format(n=n),
                target_read=_find_msg(msgs, f"{prefix}{n}{target}", write=False),
                target_write=target_write,
                current=_find_msg(msgs, f"{prefix}{n}{current}", write=False),
                mode_read=_find_msg(msgs, f"{prefix}{n}{mode}", write=False),
                mode_write=_find_msg(msgs, f"{prefix}{n}{mode}", write=True),
                shortname=_find_msg(msgs, f"{prefix}{n}Shortname", write=False),
                target_bounds=bounds,
            ))
    return out


def select_polled(
    read_messages: list[MessageDef],
    enabled_uids: set[str],
    zones: list[ClimateZone],
    prefix: str,
) -> list[MessageDef]:
    """The read messages that back at least one *enabled* entity, so only those
    are polled. A read backs a sensor, a number/select (its control id shares the
    sensor's (zz, name, label), so a writable's read twin is polled whenever its
    control is enabled) or a climate zone."""
    polled: list[MessageDef] = []
    seen: set[int] = set()

    def add(msg: MessageDef | None) -> None:
        if msg is not None and id(msg) not in seen:
            seen.add(id(msg))
            polled.append(msg)

    for m in read_messages:
        if not m.is_polled_read:
            continue
        for field in fields_of(m):
            if ({entity_uid(prefix, m, field.label), control_uid(prefix, m, field.label)}
                    & enabled_uids):
                add(m)
                break
    for zone in zones:
        if zone_uid(prefix, zone.zz, zone.n) in enabled_uids:
            for msg in (zone.target_read, zone.current, zone.mode_read, zone.shortname):
                add(msg)
    return polled
