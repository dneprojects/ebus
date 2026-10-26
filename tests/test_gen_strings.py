"""strings_gen builds per-language entity names from each definition's `tr`:
English always has a name (technical fallback), other languages only where a
translation exists (so HA falls back to English per user), and enum states use
the curated table. Loaded by path -- it imports only aioebus + stdlib."""
import importlib.util
import sys
from pathlib import Path

from aioebus.messages import FieldDef, MessageDef

_MOD = Path(__file__).resolve().parents[1] / "custom_components" / "ebus" / "strings_gen.py"
_spec = importlib.util.spec_from_file_location("ebus_strings_gen", _MOD)
sg = importlib.util.module_from_spec(_spec)
sys.modules["ebus_strings_gen"] = sg
_spec.loader.exec_module(sg)


def _msg(name, direction="r", fields=None, tr=None):
    return MessageDef(direction=direction, circuit="", level="", name=name, comment="",
                      qq="", zz="15", pbsb="b524", msgid="00", fields=fields or [], tr=tr)


def test_names_come_from_tr_capitalized():
    m = _msg("ContinuousHeating",
             tr={"en": "continuous heating", "de": "Durchgehendes Heizen"},
             fields=[FieldDef("value", "", "EXP", None, None, "°C", "")])
    en = sg.build_entities([m], "en")
    de = sg.build_entities([m], "de")
    assert en["sensor"]["continuousheating"]["name"] == "Continuous heating"
    assert de["sensor"]["continuousheating"]["name"] == "Durchgehendes Heizen"


def test_de_omitted_without_translation_so_ha_falls_back():
    m = _msg("FooBar", fields=[FieldDef("value", "", "UIN", None, None, "", "")])
    en = sg.build_entities([m], "en")
    de = sg.build_entities([m], "de")
    assert en["sensor"]["foobar"]["name"] == "FooBar"  # technical fallback
    assert "foobar" not in de["sensor"]  # no German -> HA uses English


def test_long_or_blob_comments_are_rejected():
    m = _msg("State", tr={"en": "State_X: Aktuelle Zustandsnummer der Steuerung X"},
             fields=[FieldDef("value", "", "UCH", None, None, "", "")])
    en = sg.build_entities([m], "en")
    assert en["sensor"]["state"]["name"] == "State"  # blob rejected -> technical name


def test_select_keeps_de_states_but_omits_english_name():
    m = _msg("HwcOpMode", direction="w",
             fields=[FieldDef("value", "", "UCH", None, {0: "off", 1: "auto"}, "", "")])
    en = sg.build_entities([m], "en")
    de = sg.build_entities([m], "de")
    assert en["select"]["hwcopmode"]["name"] == "HwcOpMode"
    assert "name" not in de["select"]["hwcopmode"]
    assert de["select"]["hwcopmode"]["state"] == {"off": "Aus", "auto": "Auto"}


def test_build_strings_includes_ui_section():
    data = sg.build_strings([], "de")
    assert data["config"]["step"]["user"]["title"] == "eBUS-Adapter"
    assert data["entity"] == {"sensor": {}, "number": {}, "select": {}}
