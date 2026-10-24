"""Tests for the device-resolution logic (no Home Assistant needed).

`library.py` imports only aioebus + stdlib, so it is loaded directly by path --
this exercises ident/HW matching and per-device condition evaluation without the
HA package __init__ (which needs Home Assistant installed)."""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

from aioebus.scan import ScanResult

_PKG = Path(__file__).resolve().parents[1] / "custom_components" / "ebus"
_spec = importlib.util.spec_from_file_location("ebus_library", _PKG / "library.py")
library = importlib.util.module_from_spec(_spec)
sys.modules["ebus_library"] = library
_spec.loader.exec_module(library)


def _scan(ident, hw="0000", sw="0000"):
    n = int(hw) if hw.isdigit() else None
    return ScanResult(0xB5, "Vaillant", ident, sw, hw, int(sw) if sw.isdigit() else None, n)


def test_resolve_bundled_by_ident_and_hw():
    assert library.resolve_file("V32", "9802") == "eloblock.json"   # HW variant
    assert library.resolve_file("V32", "6004") == "v32.hp.json"     # other V32 = heat pump
    assert library.resolve_file("NETX3", "0404") == "f6.netx3.json"
    assert library.resolve_file("HMU00", "5103") == "hmu.json"
    assert library.resolve_file("CTLV3", "8004") == "ctlv3.json"    # re-engineered
    assert library.resolve_file("PMW01", "0000") == "pmw.json"
    assert library.resolve_file("VR_71", "0000") == "vr_71.json"
    assert library.resolve_file("Broadcast", "") == "broadcast.json"
    assert library.resolve_file("V32", "1111") is None              # unknown HW variant
    assert library.resolve_file("UNKNOWN", "0000") is None


def test_device_name_disambiguates_shared_ident():
    # eloBlock and the heat pump both scan as "V32" but must get distinct names
    assert library.device_name(_scan("V32", "9802")) == "eloBlock"
    assert library.device_name(_scan("V32", "6004")) == "Wärmepumpe 2"
    assert library.device_name(_scan("HMU00", "5103")) == "Wärmepumpe"
    # no mapped name -> fall back to the scan ident
    assert library.device_name(_scan("CTLV3", "8004")) == "CTLV3"


def test_climate_zones_from_index():
    # the 'climate' declaration in index.json must bundle the Z<n> registers
    devices = {0x15: _scan("CTLV3", "8004", "808")}
    zones = {z.n: z for z in library.climate_zones(devices)}
    assert set(zones) == {1, 2, 3}
    z1 = zones[1]
    assert z1.name == "Heizkreis 1" and z1.zz == "15"
    assert z1.target_write is not None and z1.target_write.name == "Z1DayTemp"
    assert z1.current is not None and z1.current.name == "Z1RoomTemp"
    assert z1.mode_read is not None and z1.mode_read.name == "Z1OpMode"
    # no climate declaration for a device without one -> no zones
    assert library.climate_zones({0x08: _scan("HMU00", "5103")}) == []


def test_timer_schedules_convention():
    # weekly time programs are detected from the <prefix>Timer_<Weekday><slot>
    # register names alone -- no declaration
    devices = {0x15: _scan("CTLV3", "8004", "808")}
    sched = library.timer_schedules(devices)
    prefixes = {prefix for (_zz, prefix) in sched}
    assert {"Hwc", "Z1", "Z2"} <= prefixes  # hot water + the active heating zones
    hwc = sched[("15", "Hwc")]
    assert len(hwc) >= 7 and all(s.msg.is_read for s in hwc)


def test_resolve_user_file_prefix_and_variant():
    d = Path(tempfile.mkdtemp())
    for name in ("15.ctlv3.json", "08.hmu.json", "08.hmu.HW5103.json"):
        (d / name).write_text("[]", encoding="utf-8")
    # ident prefix: "HMU00" -> files with ident "hmu"; HW 5103 -> the HW variant
    assert library.resolve_user_file(_scan("HMU00", "5103"), d).name == "08.hmu.HW5103.json"
    # other HW -> the plain base file
    assert library.resolve_user_file(_scan("HMU00", "9999"), d).name == "08.hmu.json"
    # exact ident
    assert library.resolve_user_file(_scan("CTLV3"), d).name == "15.ctlv3.json"
    assert library.resolve_user_file(_scan("NOPE"), d) is None


def test_per_device_condition_resolution():
    d = Path(tempfile.mkdtemp())
    defs = [
        {"direction": "r1", "name": "Foo", "pbsb": "b509", "id": "0d0500",
         "conditions": ["id_sw>=200"], "fields": [{"name": "value", "type": "UCH"}]},
        {"direction": "r", "name": "Foo", "pbsb": "b509", "id": "0d0500",
         "fields": [{"name": "value", "type": "D2C"}]},
        {"direction": "r", "name": "New", "pbsb": "b509", "id": "0d0600",
         "conditions": ["id_sw>=999"], "fields": [{"name": "value", "type": "UCH"}]},
    ]
    (d / "15.test.json").write_text(json.dumps(defs), encoding="utf-8")

    hi = {m.name: m for m in library.addressed_messages(0x15, _scan("TEST", sw="808"), d)}
    assert hi["Foo"].fields[0].type == "UCH"   # matched variant wins
    assert "New" not in hi                      # id_sw>=999 false -> dropped
    assert hi["Foo"].zz == "15"                 # address stamped at runtime

    lo = {m.name: m for m in library.addressed_messages(0x15, _scan("TEST", sw="100"), d)}
    assert lo["Foo"].fields[0].type == "D2C"   # falls back to unconditional base


def test_device_messages_direction_and_dedup():
    devices = {0x38: _scan("V32", "9802")}   # resolves to our bundled eloblock
    reads = library.device_messages(devices, "r")
    assert reads and all(m.is_read and m.zz == "38" for m in reads)
    assert len({(m.zz, m.name) for m in reads}) == len(reads)  # de-duplicated


def test_broadcast_updates_surface_as_reads():
    # the bundled broadcast circuit uses update (u) messages; they must appear
    # as read entities (sensors) and never as writes
    devices = {0xFE: _scan("Broadcast", hw="", sw="")}
    reads = library.device_messages(devices, "r")
    writes = library.device_messages(devices, "w")
    names = {m.name for m in reads}
    assert "Outsidetemp" in names
    assert all(m.zz == "fe" for m in reads)
    assert not writes
