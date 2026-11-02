"""Device resolution and entity decisions (no Home Assistant needed).

definitions/entities/ids import only aioebus + stdlib, so they are loaded without
the HA package __init__ (see pure_loader) -- this exercises ident/HW matching,
per-device condition evaluation and the entity classification."""
import json
import tempfile
from pathlib import Path

from aioebus.scan import ScanResult

from pure_loader import PKG as _PKG
from pure_loader import pure

definitions = pure("definitions")
entities = pure("entities")
ids = pure("ids")


def _scan(ident, hw="0000", sw="0000"):
    n = int(hw) if hw.isdigit() else None
    return ScanResult(0xB5, "Vaillant", ident, sw, hw, int(sw) if sw.isdigit() else None, n)


def test_resolve_bundled_by_ident_and_hw():
    assert definitions.resolve_file("V32", "9802") == "eloblock.json"   # HW variant
    # the heat pump behind a V32 coupler is the same HMU -> the same one file
    assert definitions.resolve_file("V32", "6004") == "hmu.json"
    assert definitions.resolve_file("NETX3", "0404") == "f6.netx3.json"
    assert definitions.resolve_file("HMU00", "5103") == "hmu.json"
    assert definitions.resolve_file("CTLV3", "8004") == "ctlv3.json"    # re-engineered
    assert definitions.resolve_file("PMW01", "0000") == "pmw.json"
    assert definitions.resolve_file("VR_71", "0000") == "vr_71.json"
    assert definitions.resolve_file("Broadcast", "") == "broadcast.json"
    assert definitions.resolve_file("V32", "1111") is None              # unknown HW variant
    assert definitions.resolve_file("UNKNOWN", "0000") is None


def test_device_name_disambiguates_shared_ident():
    # eloBlock and the heat pump both scan as "V32" but must get distinct names
    assert entities.device_name(_scan("V32", "9802")) == "eloBlock"
    assert entities.device_name(_scan("V32", "6004")) == "Wärmepumpe 2"
    assert entities.device_name(_scan("HMU00", "5103")) == "Wärmepumpe"
    assert entities.device_name(_scan("VR_71", "5904")) == "VR 71"
    assert entities.device_name(_scan("CTLV3", "8004")) == "sensoCOMFORT"
    # no mapped name -> fall back to the scan ident
    assert entities.device_name(_scan("PMW01", "8302")) == "PMW01"


def test_device_model_overrides_coupler_ident():
    # both scan as "V32" (the coupler); the model shows the actual appliance
    assert entities.device_model(_scan("V32", "6004")) == "aroTHERM plus"
    assert entities.device_model(_scan("V32", "9802")) == "eloBlock"
    # devices that report their own ident keep it as the model
    assert entities.device_model(_scan("VR_71", "5904")) == "Mischer- und Solarmodul"
    assert entities.device_model(_scan("CTLV3", "8004")) == "VRC 720"
    # a device that reports its own ident and has no override keeps it
    assert entities.device_model(_scan("HMU00", "5103")) == "HMU00"


def test_climate_zones_from_index():
    # the 'climate' declaration in index.json must bundle the Z<n> registers
    devices = {0x15: _scan("CTLV3", "8004", "808")}
    zones = {z.n: z for z in entities.climate_zones(devices)}
    assert set(zones) == {1, 2, 3}
    z1 = zones[1]
    assert z1.name == "Heizkreis 1" and z1.zz == "15"
    assert z1.target_write is not None and z1.target_write.name == "Z1DayTemp"
    assert z1.current is not None and z1.current.name == "Z1RoomTemp"
    assert z1.mode_read is not None and z1.mode_read.name == "Z1OpMode"
    assert z1.target_bounds == (5.0, 30.0, 0.5)  # the setpoint register's own range
    # no climate declaration for a device without one -> no zones
    assert entities.climate_zones({0x08: _scan("HMU00", "5103")}) == []


def test_disabled_field_labels():
    vr = entities.disabled_field_labels(_scan("VR_71", "5904"))
    assert "SensorData1 s4" in vr and "SensorData2 s8" in vr
    # a profile carries its own list, overriding the shared file's
    hp2 = entities.disabled_field_labels(_scan("V32", "6004"))
    assert "SetMode hwctempdesired" in hp2 and "Status01 temp_2" in hp2
    assert "SetMode hwctempdesired" not in entities.disabled_field_labels(_scan("HMU00", "5103"))
    assert entities.disabled_field_labels(_scan("CTLV3", "8004")) == set()  # none declared
    assert entities.disabled_field_labels(_scan("NOPE", "0000")) == set()   # unknown device


def test_select_polled_only_enabled():
    reads = definitions.device_messages({0x15: _scan("CTLV3", "8004", "808")}, "r")
    target = next(m for m in reads if m.is_polled_read and entities.fields_of(m))
    label = entities.fields_of(target)[0].label
    assert entities.select_polled(reads, {ids.entity_uid("E1", target, label)}, [], "E1") == [target]
    # its number/select backs the read twin just the same
    assert entities.select_polled(reads, {ids.control_uid("E1", target, label)}, [], "E1") == [target]
    assert entities.select_polled(reads, set(), [], "E1") == []        # nothing enabled
    assert entities.select_polled(reads, {ids.entity_uid("E1", target, label)}, [], "E2") == []


def test_resolve_user_file_prefix_and_variant():
    d = Path(tempfile.mkdtemp())
    for name in ("15.ctlv3.json", "08.hmu.json", "08.hmu.HW5103.json"):
        (d / name).write_text("[]", encoding="utf-8")
    # ident prefix: "HMU00" -> files with ident "hmu"; HW 5103 -> the HW variant
    assert definitions.resolve_user_file(_scan("HMU00", "5103"), d).name == "08.hmu.HW5103.json"
    # other HW -> the plain base file
    assert definitions.resolve_user_file(_scan("HMU00", "9999"), d).name == "08.hmu.json"
    # exact ident
    assert definitions.resolve_user_file(_scan("CTLV3"), d).name == "15.ctlv3.json"
    assert definitions.resolve_user_file(_scan("NOPE"), d) is None


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

    hi = {m.name: m for m in definitions.addressed_messages(0x15, _scan("TEST", sw="808"), d)}
    assert hi["Foo"].fields[0].type == "UCH"   # matched variant wins
    assert "New" not in hi                      # id_sw>=999 false -> dropped
    assert hi["Foo"].zz == "15"                 # address stamped at runtime

    lo = {m.name: m for m in definitions.addressed_messages(0x15, _scan("TEST", sw="100"), d)}
    assert lo["Foo"].fields[0].type == "D2C"   # falls back to unconditional base


def test_device_messages_direction_and_dedup():
    devices = {0x38: _scan("V32", "9802")}   # resolves to our bundled eloblock
    reads = definitions.device_messages(devices, "r")
    assert reads and all(m.is_read and m.zz == "38" for m in reads)
    assert len({(m.zz, m.name) for m in reads}) == len(reads)  # de-duplicated


def test_broadcast_updates_surface_as_reads():
    # the bundled broadcast circuit uses update (u) messages; they must appear
    # as read entities (sensors) and never as writes
    devices = {0xFE: _scan("Broadcast", hw="", sw="")}
    reads = definitions.device_messages(devices, "r")
    writes = definitions.device_messages(devices, "w")
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

    # and no register is defined twice under different names
    reg = [(m.pbsb, m.msgid, m.direction, tuple(m.conditions or ()))
           for m in from_dicts(json.loads(hmu.read_text(encoding="utf-8")))]
    assert len(reg) == len(set(reg))


def test_both_heat_pumps_share_the_full_definition():
    # the HMU at 08 and the same HMU behind the V32 coupler at 18 get the same
    # message set (incl. the energy counters), each stamped with its own address.
    # Real firmware (sw 0902) so the conditional registers are in play too.
    devices = {0x08: _scan("HMU00", "5103", "0902"), 0x18: _scan("V32", "6004", "0902")}
    reads = definitions.device_messages(devices, "r")
    by_zz = {zz: {m.name for m in reads if m.zz == zz} for zz in ("08", "18")}
    assert by_zz["08"] == by_zz["18"]
    assert {"EnergyGeneratedHc", "PowerConsumptionHmu", "Status01",
            "CompressorHc"} <= by_zz["18"]


def test_disabled_fields_add_up():
    # the profile's own entry adds to the file's unconnected channels
    wp2 = entities.disabled_field_labels(_scan("V32", "6004"))
    wp1 = entities.disabled_field_labels(_scan("HMU00", "5103"))
    assert "Status01 temp_2" in wp1 and "SetMode hwctempdesired" not in wp1
    assert wp1 | {"SetMode hwctempdesired"} == wp2


def test_preload_leaves_no_file_io(monkeypatch):
    # after preload (run in an executor) the definitions come from memory: nothing
    # it does later in the event loop may touch the disk
    devices = {0x15: _scan("CTLV3", "8004", "808"), 0x08: _scan("HMU00", "5103", "0902")}
    d = Path(tempfile.mkdtemp())
    (d / "26.vr71x.json").write_text("[]", encoding="utf-8")
    devices[0x26] = _scan("VR71X")  # covered by the user directory
    definitions.preload(devices, d)

    def no_io(*_a, **_k):
        raise AssertionError("file I/O after preload")

    monkeypatch.setattr(Path, "read_text", no_io)
    monkeypatch.setattr(Path, "glob", no_io)
    definitions.all_messages(devices, d)
    entities.climate_zones(devices, d)
    entities.disabled_field_labels(devices[0x08])


def test_controls_are_classified_once_for_platforms_and_names():
    from aioebus.messages import FieldDef, MessageDef

    def msg(direction, *fields):
        return MessageDef(direction, "", "", "M", "", "", "15", "b524", "00", list(fields))

    num = FieldDef("value", "", "D2C", None, None, "", "")
    enum = FieldDef("value", "", "UCH", None, {0: "off", 1: "on"}, "", "")
    text = FieldDef("value", "", "STR:5", None, None, "", "")
    assert entities.control_of(msg("w", num))[0] == "number"
    assert entities.control_of(msg("w", enum))[0] == "select"
    assert entities.control_of(msg("w", text)) is None
    assert entities.control_of(msg("r", num)) is None           # reads get no control
    assert entities.control_of(msg("w", num, num)) is None      # only single-field writes
    assert entities.has_numeric_value("BCD") and not entities.is_number_type("BCD")
    assert entities.is_number_type("EXP") and not entities.has_numeric_value("HEX:2")


def test_index_json_names_only_what_the_files_define():
    # every file the index points to exists; control classes and number bounds
    # name writable registers of that file; disabled fields are entity labels
    index = definitions.index()
    files = {p.name for p in definitions.CONFIG_DIR.glob("*.json")} - {"index.json"}
    referenced = set()
    for key, entry in index.items():
        if key.startswith("_") or not isinstance(entry, (str, dict)):
            continue
        if isinstance(entry, str) and entry.endswith(".json"):
            referenced.add(entry)
        elif isinstance(entry, dict) and "file" in entry:
            referenced.add(entry["file"])
        elif isinstance(entry, dict):
            referenced |= {v["file"] if isinstance(v, dict) else v
                           for v in [*entry.get("hw", {}).values(), entry.get("default")]
                           if v and (isinstance(v, str) or "file" in v)}
    assert referenced <= files, referenced - files
    for section in ("controls", "number_bounds"):
        for file, table in index.get(section, {}).items():
            if file.startswith("_"):
                continue
            writable = {m.name for m in definitions.load_file(file) if m.is_write}
            names = set(table) if section == "number_bounds" else {
                n for cls in ("user", "installer") for n in table.get(cls, ())}
            assert names <= writable, (section, file, names - writable)
    for file, labels in index.get("disabled_fields", {}).items():
        if file.startswith("_"):
            continue
        known = {f.label for m in definitions.load_file(file) for f in entities.fields_of(m)}
        assert set(labels) <= known, (file, set(labels) - known)


def test_shipped_translations_match_the_generator():
    # strings.json and translations/{en,de}.json are generated (tools/gen_strings.py):
    # an edit to a definition or a name must come with regenerated files
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gen_strings", _PKG.parents[1] / "tools" / "gen_strings.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    for rel, data in gen.build().items():
        assert (_PKG / rel).read_text(encoding="utf-8") == gen.render(data), rel


def test_every_bundled_entity_has_a_german_name():
    sg = pure("strings_gen")
    msgs = [m for p in definitions.CONFIG_DIR.glob("*.json") if p.name != "index.json"
            for m in definitions.load_file(p.name)]
    en, de = sg.build_entities(msgs, "en"), sg.build_entities(msgs, "de")
    for platform, keys in en.items():
        missing = {k for k in keys if "name" not in de[platform].get(k, {})}
        assert not missing, (platform, sorted(missing)[:10])
