"""What Home Assistant entities a device's messages become.

One home for the decisions the platforms, the polling scope and the translation
generator share: a writable register's control class, a device's name, icons,
what is polled. What a value is (numeric, the control a write allows) comes from
aioebus.values, the device facts (definition, model, ranges, heating zones) from
the aioebus catalog; how this integration presents them is in
``presentation.json``. Imports only aioebus, the
standard library and other pure modules of this package, so tools and pure
tests can load it without Home Assistant.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from functools import cache
from pathlib import Path
from typing import Any, Final

from aioebus.catalog import ClimateZone, Definition, bundled_file, bundled_file_of
from aioebus.codecs import value_range
from aioebus.messages import FieldDef, MessageDef
from aioebus.scan import ScanResult
from aioebus.values import values_of

from .ids import control_uid, entity_uid, zone_state_uid, zone_uid
from .zone_state import messages_of as state_messages

PRESENTATION: Final = Path(__file__).parent / "presentation.json"

# what a register's read value without content means, by message: the state shown
# (a device answers 0xffff in every slot of its error list while it has no error)
NO_VALUE_STATES: Final = {"Currenterror": "no_error"}

# a heat pump's coefficient of performance: its heat output over its electric
# power (both kW); below this power it only keeps its electronics alive
COP_HEAT: Final = "RunDataHeatOutput"
COP_POWER: Final = "PowerConsumptionHmu"
COP_MIN_POWER_KW: Final = 0.06

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


@cache
def presentation() -> dict[str, Any]:
    """presentation.json (blocking read: load it in an executor first)."""
    data: dict[str, Any] = json.loads(PRESENTATION.read_text(encoding="utf-8"))
    return data


def _keys(definition: Definition | None) -> list[str]:
    """presentation.json keys of a bundled definition: its profile, then its file."""
    if definition is None or definition.source != "bundled":
        return []
    return [k for k in (definition.profile, definition.file) if k]


def control_class(definition: Definition | None, message: str) -> str:
    """What a writable register becomes: "user" (a control, enabled), "installer"
    (a configuration control, created disabled) or "none" (no control, the
    sensor only), from the 'controls' section of presentation.json. A reviewed
    file lists its user and installer registers, so anything unlisted (a counter,
    a live state) gets no control; a device without a section (a user or CDN
    definition) gets configuration controls the user enables deliberately."""
    sections = presentation()["controls"]
    section = next((sections[k] for k in _keys(definition) if k in sections), None)
    if not isinstance(section, dict):
        return "installer"
    for cls in ("user", "installer"):
        if message in section.get(cls, ()):
            return cls
    return "none"


def preload_controls() -> None:
    """Read presentation.json and the bundled files its controls name (blocking:
    run it in an executor before bundled_control_class is used in the loop)."""
    for key in presentation()["controls"]:
        if not key.startswith("_") and (file := bundled_file_of(key)) is not None:
            bundled_file(file)


def bundled_control_class(message: str) -> str | None:
    """The class of a register name across the reviewed bundled files (for
    migrating entities registered before the classes existed): the most
    permissive class it has anywhere, "none" if those files define it without
    listing it, None if none of them defines it (a user definition)."""
    classes: set[str] = set()
    for key, section in presentation()["controls"].items():
        if key.startswith("_") or not isinstance(section, dict):
            continue
        file = bundled_file_of(key)
        if file is None:
            raise ValueError(f"presentation.json: no bundled file or profile {key!r}")
        if message not in {m.name for m in bundled_file(file)}:
            continue
        classes.add(next((c for c in ("user", "installer") if message in section.get(c, ())),
                         "none"))
    for cls in ("user", "installer", "none"):
        if cls in classes:
            return cls
    return None


def number_bounds(listed: tuple[float, float, float] | None, field: FieldDef
                  ) -> tuple[float, float, float]:
    """(min, max, step) of a number: the catalog's listed range, else a unit
    default for a float register, else the range its integer type can carry."""
    if listed is not None:
        return listed
    if (own := value_range(field.type, field.divisor)) is not None:  # integer types
        lo, hi, step = own
        return (lo, hi, max(round(step, SHOWN_DECIMALS), 10 ** -SHOWN_DECIMALS))
    return _UNIT_BOUNDS.get(field.unit or "", _UNIT_BOUNDS[""])


SHOWN_DECIMALS: Final = 2
_WHOLE_UNITS: Final = frozenset({"kWh", "Wh"})  # energy needs no decimals


def shown(value: object, unit: str | None = None) -> object:
    """A value as an entity reports it: a fraction with at most two decimals,
    an energy figure as a whole number."""
    if not isinstance(value, float):
        return value
    return round(value) if unit in _WHOLE_UNITS else round(value, SHOWN_DECIMALS)


def options_of(field: FieldDef) -> list[str]:
    """The labels of a value-table field, each once (two numbers may share one)."""
    return list(dict.fromkeys((field.values or {}).values()))


def disabled_field_labels(definition: Definition | None) -> set[str]:
    """Entity labels created disabled for this device (unconnected channels):
    the file's own plus those its profile adds."""
    table = presentation()["disabled_fields"]
    out: set[str] = set()
    for key in _keys(definition):
        if isinstance(labels := table.get(key), list):
            out.update(labels)
    return out


def device_name(scan: ScanResult, definition: Definition | None) -> str:
    """Friendly device name: a profile's or a file's name from presentation.json
    (two devices can share a scan ident or a file), else the scan ident."""
    names = presentation()["names"]
    return next((names[k] for k in _keys(definition) if k in names), scan.ident)


def device_model(scan: ScanResult, definition: Definition | None) -> str:
    """Model shown in HA: the appliance the catalog knows (a device behind a bus
    coupler scans as the coupler), else the scan ident."""
    return definition.model if definition is not None and definition.model else scan.ident


def icon_for(field: FieldDef) -> str | None:
    if field.unit in _UNIT_ICON:
        return _UNIT_ICON[field.unit]
    name = field.name.lower()
    for keyword, icon in _KEYWORD_ICON:
        if keyword in name:
            return icon
    return None


def cop_of(heat: object, power: object) -> float | None:
    """The coefficient of performance now; none while the compressor delivers no
    heat (standby, defrost)."""
    if not isinstance(heat, int | float) or not isinstance(power, int | float):
        return None
    return round(heat / power, 2) if power >= COP_MIN_POWER_KW and heat > 0 else None


def cop_sources(messages: Iterable[MessageDef]) -> tuple[MessageDef, MessageDef] | None:
    """A heat pump's registers of its heat output and electric power, if it has
    both (same unit, read together)."""
    reads = {m.name: m for m in messages if m.is_read}
    heat, power = reads.get(COP_HEAT), reads.get(COP_POWER)
    return (heat, power) if heat is not None and power is not None else None


def select_polled(
    read_messages: list[MessageDef],
    enabled_uids: set[str],
    zones: list[ClimateZone],
    prefix: str,
    backed: Iterable[tuple[str, Iterable[MessageDef | None]]] = (),
) -> list[MessageDef]:
    """The read messages that back at least one *enabled* entity, so only those
    are polled. A read backs a sensor, a number/select (its control id shares the
    sensor's (zz, name, label), so a writable's read twin is polled whenever its
    control is enabled), a climate zone or a zone's state sensor, or one of the
    further entities ``backed`` lists with the registers they show."""
    polled: list[MessageDef] = []
    seen: set[int] = set()

    def add(msg: MessageDef | None) -> None:
        if msg is not None and id(msg) not in seen:
            seen.add(id(msg))
            polled.append(msg)

    for m in read_messages:  # the scheduler drops what cannot be polled
        for field in values_of(m):
            if ({entity_uid(prefix, m, field.label), control_uid(prefix, m, field.label)}
                    & enabled_uids):
                add(m)
                break
    for zone in zones:
        if zone_uid(prefix, zone.zz, zone.n) in enabled_uids:
            for msg in (zone.target_read, zone.current, zone.mode_read, zone.shortname,
                        zone.setpoint, zone.special_read, zone.holiday_start,
                        zone.holiday_end):
                add(msg)
        if zone_state_uid(prefix, zone.zz, zone.n) in enabled_uids:
            for msg in state_messages(zone):
                add(msg)
    for unique_id, messages in backed:
        if unique_id in enabled_uids:
            for msg in messages:
                add(msg)
    return polled
