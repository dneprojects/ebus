"""The strings generator takes entity names from each definition's `tr`, emits a
German name only where one exists (so HA falls back to English otherwise), and
keeps translated enum states. Loaded by path -- it is a tool, not a package."""
import importlib.util
import sys
from pathlib import Path

from aioebus.messages import FieldDef, MessageDef

_TOOL = Path(__file__).resolve().parents[1] / "tools" / "gen_strings.py"
_spec = importlib.util.spec_from_file_location("gen_strings", _TOOL)
gen = importlib.util.module_from_spec(_spec)
sys.modules["gen_strings"] = gen
_spec.loader.exec_module(gen)


def _msg(name, direction="r", fields=None, tr=None):
    return MessageDef(direction=direction, circuit="", level="", name=name, comment="",
                      qq="", zz="15", pbsb="b524", msgid="00", fields=fields or [], tr=tr)


def test_names_come_from_tr_capitalized():
    m = _msg("ContinuousHeating",
             tr={"en": "continuous heating", "de": "Durchgehendes Heizen"},
             fields=[FieldDef("value", "", "EXP", None, None, "°C", "")])
    en, de = gen.build_entities([m])
    assert en["entity"]["sensor"]["continuousheating"]["name"] == "Continuous heating"
    assert de["entity"]["sensor"]["continuousheating"]["name"] == "Durchgehendes Heizen"


def test_de_omitted_without_translation_so_ha_falls_back_to_english():
    m = _msg("FooBar", fields=[FieldDef("value", "", "UIN", None, None, "", "")])
    en, de = gen.build_entities([m])
    assert en["entity"]["sensor"]["foobar"]["name"] == "FooBar"  # technical fallback
    assert "foobar" not in de["entity"]["sensor"]  # no German -> HA uses English


def test_select_keeps_de_states_but_omits_english_name():
    m = _msg("HwcOpMode", direction="w",
             fields=[FieldDef("value", "", "UCH", None, {0: "off", 1: "auto"}, "", "")])
    en, de = gen.build_entities([m])
    assert en["entity"]["select"]["hwcopmode"]["name"] == "HwcOpMode"
    assert "name" not in de["entity"]["select"]["hwcopmode"]  # no fake German name
    assert de["entity"]["select"]["hwcopmode"]["state"] == {"off": "Aus", "auto": "Auto"}
