"""How the integration presents the catalog's devices (no Home Assistant needed).

entities/ids/strings_gen import only aioebus + stdlib, so they are loaded without
the HA package __init__ (see pure_loader). Which definition a device has is the
aioebus catalog's business and tested there."""
import asyncio
import importlib.util

from aioebus.catalog import (
    Catalog,
    bundled_file,
    bundled_files,
    bundled_index,
    resolve_bundled,
)
from aioebus.scan import ScanResult
from aioebus.values import values_of

from pure_loader import PKG, pure

entities = pure("entities")
ids = pure("ids")


def _scan(ident, hw="0000", sw="0000"):
    n = int(hw) if hw.isdigit() else None
    return ScanResult(0xB5, "Vaillant", ident, sw, hw, int(sw) if sw.isdigit() else None, n)


def _named(ident, hw="0000"):
    scan = _scan(ident, hw)
    return entities.device_name(scan, resolve_bundled(scan))


def _model(ident, hw="0000"):
    scan = _scan(ident, hw)
    return entities.device_model(scan, resolve_bundled(scan))


def test_device_name_disambiguates_shared_ident():
    # eloBlock and the heat pump both scan as "V32" but must get distinct names
    assert _named("V32", "9802") == "eloBlock"
    assert _named("V32", "6004") == "Wärmepumpe 2"
    assert _named("HMU00", "5103") == "Wärmepumpe"
    assert _named("VR_71", "5904") == "VR 71"
    assert _named("CTLV3", "8004") == "sensoCOMFORT"
    assert _named("PMW01", "8302") == "PMW01"  # no name -> the scan ident
    assert entities.device_name(_scan("XYZ"), None) == "XYZ"  # no definition at all


def test_device_model_overrides_coupler_ident():
    assert _model("V32", "6004") == "aroTHERM plus"
    assert _model("V32", "9802") == "eloBlock"
    assert _model("VR_71", "5904") == "Mischer- und Solarmodul"
    assert _model("CTLV3", "8004") == "VRC 720"
    assert _model("HMU00", "5103") == "HMU00"


def test_disabled_fields_add_up():
    def labels(ident, hw="0000"):
        return entities.disabled_field_labels(resolve_bundled(_scan(ident, hw)))

    assert "SensorData1 s4" in labels("VR_71", "5904")
    wp1, wp2 = labels("HMU00", "5103"), labels("V32", "6004")
    assert "Status01 temp_2" in wp1 and "SetMode hwctempdesired" not in wp1
    assert wp1 | {"SetMode hwctempdesired"} == wp2  # the profile adds to its file
    assert labels("CTLV3", "8004") == set()
    assert entities.disabled_field_labels(None) == set()


def test_control_classes_by_reviewed_file():
    ctl = resolve_bundled(_scan("CTLV3", "8004"))
    assert entities.control_class(ctl, "HwcOpMode") == "user"
    assert entities.control_class(ctl, "HydraulicScheme") == "installer"
    assert entities.control_class(ctl, "PrEnergySumHc") == "none"
    assert entities.control_class(None, "Anything") == "installer"  # user/CDN definition
    assert entities.bundled_control_class("PrEnergySumHc") == "none"
    assert entities.bundled_control_class("SomethingUserDefined") is None


def test_select_polled_only_enabled():
    catalog = Catalog()
    asyncio.run(catalog.load({0x15: _scan("CTLV3", "8004", "808")}))
    reads = catalog.device_messages("r")
    target = next(m for m in reads if m.is_polled_read and values_of(m))
    label = values_of(target)[0].label
    assert entities.select_polled(reads, {ids.entity_uid("E1", target, label)}, [], "E1") == [target]
    # its number/select backs the read twin just the same
    assert entities.select_polled(reads, {ids.control_uid("E1", target, label)}, [], "E1") == [target]
    assert entities.select_polled(reads, set(), [], "E1") == []        # nothing enabled
    assert entities.select_polled(reads, {ids.entity_uid("E1", target, label)}, [], "E2") == []


def test_presentation_names_only_what_the_catalog_defines():
    # names/controls/disabled fields are keyed by bundled files or IDENT/HW profiles;
    # controls name writable registers; disabled fields are entity labels
    pres = entities.presentation()
    files = set(bundled_files())
    profiles = {f"{ident}/{hw}" for ident, entry in bundled_index()["devices"].items()
                if isinstance(entry, dict) for hw in entry.get("hw", {})}
    for section in ("names", "controls", "disabled_fields"):
        keys = {k for k in pres[section] if not k.startswith("_")}
        assert keys <= files | profiles, (section, keys - files - profiles)

    def file_of(key):
        if key in files:
            return key
        ident, hw = key.split("/")
        return resolve_bundled(_scan(ident, hw)).file

    for key, table in pres["controls"].items():
        if key.startswith("_"):
            continue
        writable = {m.name for m in bundled_file(file_of(key)) if m.is_write}
        names = {n for cls in ("user", "installer") for n in table.get(cls, ())}
        assert names <= writable, (key, names - writable)
    for key, labels in pres["disabled_fields"].items():
        if key.startswith("_"):
            continue
        known = {f.label for m in bundled_file(file_of(key)) for f in values_of(m)}
        assert set(labels) <= known, (key, set(labels) - known)


def test_shipped_translations_match_the_generator():
    # strings.json and translations/{en,de}.json are generated (tools/gen_strings.py):
    # an edit to a definition or a name must come with regenerated files
    spec = importlib.util.spec_from_file_location(
        "gen_strings", PKG.parents[1] / "tools" / "gen_strings.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    for rel, data in gen.build().items():
        assert (PKG / rel).read_text(encoding="utf-8") == gen.render(data), rel


def test_every_bundled_entity_has_a_german_name():
    sg = pure("strings_gen")
    msgs = [m for name in bundled_files() for m in bundled_file(name)]
    en, de = sg.build_entities(msgs, "en"), sg.build_entities(msgs, "de")
    for platform, keys in en.items():
        missing = {k for k in keys if "name" not in de[platform].get(k, {})}
        assert not missing, (platform, sorted(missing)[:10])
