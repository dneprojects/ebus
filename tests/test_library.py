"""Tests for the device-resolution logic (no Home Assistant needed).

`library.py` imports only aioebus + stdlib, so it is loaded directly by path --
this exercises ident/HW matching and per-device condition evaluation without the
HA package __init__ (which needs Home Assistant installed)."""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

from aioebus.engine import value_key
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
    # the heat pump behind a V32 coupler is the same HMU -> the same one file
    assert library.resolve_file("V32", "6004") == "hmu.json"
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
    assert library.device_name(_scan("VR_71", "5904")) == "VR 71"
    assert library.device_name(_scan("CTLV3", "8004")) == "sensoCOMFORT"
    # no mapped name -> fall back to the scan ident
    assert library.device_name(_scan("PMW01", "8302")) == "PMW01"


def test_device_model_overrides_coupler_ident():
    # both scan as "V32" (the coupler); the model shows the actual appliance
    assert library.device_model(_scan("V32", "6004")) == "aroTHERM plus"
    assert library.device_model(_scan("V32", "9802")) == "eloBlock"
    # devices that report their own ident keep it as the model
    assert library.device_model(_scan("VR_71", "5904")) == "Mischer- und Solarmodul"
    assert library.device_model(_scan("CTLV3", "8004")) == "VRC 720"
    # a device that reports its own ident and has no override keeps it
    assert library.device_model(_scan("HMU00", "5103")) == "HMU00"


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


def test_disabled_field_labels():
    vr = library.disabled_field_labels(_scan("VR_71", "5904"))
    assert "SensorData1 s4" in vr and "SensorData2 s8" in vr
    # a profile carries its own list, overriding the shared file's
    hp2 = library.disabled_field_labels(_scan("V32", "6004"))
    assert "SetMode hwctempdesired" in hp2 and "Status01 temp_2" in hp2
    assert "SetMode hwctempdesired" not in library.disabled_field_labels(_scan("HMU00", "5103"))
    assert library.disabled_field_labels(_scan("CTLV3", "8004")) == set()  # none declared
    assert library.disabled_field_labels(_scan("NOPE", "0000")) == set()   # unknown device


def test_select_polled_only_enabled():
    reads = library.device_messages({0x15: _scan("CTLV3", "8004", "808")}, "r")
    target = next(m for m in reads if m.direction[:1] == "r" and library.fields_of(m))
    uid = "ebus_" + "_".join(value_key(target, library.fields_of(target)[0].label))
    assert library.select_polled(reads, {uid}, [], {}) == [target]  # only the enabled one
    assert library.select_polled(reads, set(), [], {}) == []         # nothing enabled


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


def test_label_for_localizes_from_tr_with_fallback():
    # a message whose lone 'value' field carries German/English display text
    defs = [
        {"direction": "r", "name": "ContinuousHeating", "pbsb": "b524", "id": "020000000200",
         "tr": {"en": "continuous heating", "de": "Durchgehendes Heizen"},
         "fields": [{"name": "value", "type": "EXP", "unit": "°C",
                     "tr": {"en": "continuous heating", "de": "Durchgehendes Heizen"}}]},
        {"direction": "r", "name": "Plain", "pbsb": "b524", "id": "020000000300",
         "fields": [{"name": "value", "type": "UIN"}]},  # no tr -> technical fallback
    ]
    d = Path(tempfile.mkdtemp())
    (d / "15.test.json").write_text(json.dumps(defs), encoding="utf-8")
    msgs = {m.name: m for m in library.addressed_messages(0x15, _scan("TEST"), d)}

    f = library.fields_of(msgs["ContinuousHeating"])[0]
    assert library.label_for(f, "de") == "Durchgehendes Heizen"
    assert library.label_for(f, "en") == "continuous heating"
    assert library.label_for(f, "fr") == "continuous heating"  # -> en fallback

    plain = library.fields_of(msgs["Plain"])[0]
    assert library.label_for(plain, "de") == "Plain"  # no tr -> technical label


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


def test_hmu_energy_counters_decode_kwh():
    # the bundled HMU config exposes the nine b516/10 lifetime energy counters the
    # sensoNET gateway polls; EXP + divisor must yield kWh (needs aioebus >= 0.5.1).
    # Real 24h-log capture: EnergyEnvironmentHc slave trailing float -> kWh/1000.
    from aioebus.convert import from_dicts
    hmu = _PKG / "configs" / "vaillant" / "hmu.json"
    defs = {m.name: m for m in from_dicts(json.loads(hmu.read_text(encoding="utf-8")))}
    energy = {n for n, m in defs.items() if n.startswith("Energy") and m.pbsb == "b516"}
    assert energy == {f"Energy{s}{u}" for s in ("Environment", "Generated", "Electric")
                      for u in ("Hc", "Hwc", "Cool")}
    m = defs["EnergyEnvironmentHc"]
    assert m.decode_labeled("1000ffff42030000", "00000142035e35372f9f4b") == {
        "EnergyEnvironmentHc": 20864.622}
    # one home per value: retired and renamed registers are gone from the bundle
    assert not (library.RETIRED_MESSAGES | set(library.RENAMED_MESSAGES)) & set(defs)
    assert set(library.RENAMED_MESSAGES.values()) <= set(defs)
    # and no register is defined twice under different names
    reg = [(m.pbsb, m.msgid, m.direction, tuple(m.conditions or ()))
           for m in from_dicts(json.loads(hmu.read_text(encoding="utf-8")))]
    assert len(reg) == len(set(reg))


def test_both_heat_pumps_share_the_full_definition():
    # the HMU at 08 and the same HMU behind the V32 coupler at 18 get the same
    # message set (incl. the energy counters), each stamped with its own address.
    # Real firmware (sw 0902) so the conditional registers are in play too.
    devices = {0x08: _scan("HMU00", "5103", "0902"), 0x18: _scan("V32", "6004", "0902")}
    reads = library.device_messages(devices, "r")
    by_zz = {zz: {m.name for m in reads if m.zz == zz} for zz in ("08", "18")}
    assert by_zz["08"] == by_zz["18"]
    assert {"EnergyGeneratedHc", "PowerConsumptionHmu", "Status01",
            "CompressorHc"} <= by_zz["18"]


def test_disabled_fields_add_up():
    # the profile's own entry adds to the file's unconnected channels
    wp2 = library.disabled_field_labels(_scan("V32", "6004"))
    wp1 = library.disabled_field_labels(_scan("HMU00", "5103"))
    assert "Status01 temp_2" in wp1 and "SetMode hwctempdesired" not in wp1
    assert wp1 | {"SetMode hwctempdesired"} == wp2


def test_retired_unique_id():
    assert library.retired_unique_id("ebus_18_YieldHc_YieldHc")
    assert not library.retired_unique_id("ebus_08_EnergyGeneratedHc_EnergyGeneratedHc")
    assert not library.retired_unique_id("ebus_08_Status01_Status01 temp")
    assert not library.retired_unique_id("ebus_192.168.178.179_9999_signal")  # adapter sensor


def test_renamed_unique_id():
    assert (library.renamed_unique_id("ebus_08_EnergyYieldHeat_EnergyYieldHeat")
            == "ebus_08_EnergyGeneratedHc_EnergyGeneratedHc")  # 0.2.0 name
    assert (library.renamed_unique_id("ebus_18_SupplyTempWeighted_SupplyTempWeighted")
            == "ebus_18_TargetFlowTemp_TargetFlowTemp")
    assert library.renamed_unique_id("ebus_08_EnergyGeneratedHc_EnergyGeneratedHc") is None
    assert library.renamed_unique_id("ebus_18_YieldHc_YieldHc") is None  # retired, not renamed


def test_scan_all_covers_known_addresses():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ebus_const", _PKG / "const.py")
    const = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(const)
    assert set(const.SCAN_ADDRESSES) <= set(const.SCAN_ALL_ADDRESSES)
    assert len(const.SCAN_ALL_ADDRESSES) == 49  # 7 high x 7 low nibble families
