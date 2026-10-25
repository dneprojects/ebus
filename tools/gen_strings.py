"""Generate strings.json (English base) and de.json (German) for the eBUS
integration from the bundled device configs, so every auto-generated entity has
a name and every enum its state labels. Run from the integration root:

    python tools/gen_strings.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "custom_components" / "ebus"
# add aioebus source if running from a source checkout (do NOT add PKG: it would
# shadow stdlib modules like `select` with the integration's platform files)
_AIOEBUS_SRC = ROOT.parent / "aioebus" / "src"
if _AIOEBUS_SRC.is_dir():
    sys.path.insert(0, str(_AIOEBUS_SRC))

from aioebus.convert import from_dicts  # noqa: E402
from aioebus.translations import _slug, translation_key  # noqa: E402

# config-flow / options strings (en + de)
CONFIG_EN = {
    "config": {
        "step": {"user": {
            "title": "eBUS adapter",
            "description": "Connect to an enhanced eBUS adapter (enh:host:port).",
            "data": {"host": "Host", "port": "Port",
                     "download_defs": "Download device definitions from ebusd-configuration"}}},
        "error": {
            "cannot_connect": "Could not reach the adapter at this host and port.",
            "no_ebus_data": "Reached the adapter, but no eBUS data is coming through. Another client (e.g. ebusd) may be using the adapter — stop it first, then retry.",
        },
        "abort": {"already_configured": "This adapter is already configured."},
    },
    "options": {"step": {"init": {
        "title": "eBUS options",
        "data": {"scan_interval": "Base poll interval (s)", "allow_write": "Allow writing settings to the bus",
                 "config_dir": "Extra device-definition directory (optional)"}}}},
}
CONFIG_DE = {
    "config": {
        "step": {"user": {
            "title": "eBUS-Adapter",
            "description": "Mit einem Enhanced-eBUS-Adapter verbinden (enh:host:port).",
            "data": {"host": "Host", "port": "Port",
                     "download_defs": "Gerätedefinitionen von ebusd-configuration laden"}}},
        "error": {
            "cannot_connect": "Adapter unter diesem Host/Port nicht erreichbar.",
            "no_ebus_data": "Adapter erreichbar, aber es kommen keine eBUS-Daten an. Vermutlich nutzt ein anderer Dienst (z. B. ebusd) den Adapter — diesen zuerst stoppen und erneut versuchen.",
        },
        "abort": {"already_configured": "Dieser Adapter ist bereits eingerichtet."},
    },
    "options": {"step": {"init": {
        "title": "eBUS-Optionen",
        "data": {"scan_interval": "Basis-Abfrageintervall (s)", "allow_write": "Schreiben von Einstellungen auf den Bus erlauben",
                 "config_dir": "Zusätzliches Definitionsverzeichnis (optional)"}}}},
}

# curated German names for the most visible registers
NAME_DE = {
    "flowtemp": "Vorlauftemperatur", "statenumber": "Status", "waterpressure": "Wasserdruck",
    "displayedoutsidetemp": "Außentemperatur", "systemflowtemp": "Anlagen-Vorlauftemperatur",
    "hc1flowtemp": "Vorlauftemperatur Heizkreis 1", "hc2flowtemp": "Vorlauftemperatur Heizkreis 2",
    "hc1actualflowtempdesired": "Vorlauf-Sollwert Heizkreis 1", "partloadhwckw": "Warmwasser-Teillast",
    "maxroomhumidity": "Max. Raumfeuchte", "returntemp": "Rücklauftemperatur",
    "hwctemp": "Warmwassertemperatur", "modulation": "Modulation",
    "electricpower": "Stromaufnahme Wärmepumpe",
    "outsidetemp": "Außentemperatur", "vdatetime": "Datum/Uhrzeit",
    "rundataelectricpowerconsumption": "Stromaufnahme Wärmepumpe",
    "yieldhcday": "Wärmeertrag Heizung (Tag)", "yieldhwcday": "Wärmeertrag Warmwasser (Tag)",
    "yieldhcmonth": "Wärmeertrag Heizung (Monat)", "cophcmonth": "Arbeitszahl Heizung (Monat)",
    "operatinghours": "Betriebsstunden",
}
# common Vaillant enum labels -> German
STATE_DE = {
    "off": "Aus", "on": "Ein", "auto": "Auto", "yes": "Ja", "no": "Nein",
    "sommer": "Sommer", "winter": "Winter", "heizen": "Heizen", "warmwasser": "Warmwasser",
    "kein_bedarf": "Kein Bedarf", "frostschutz": "Frostschutz", "pumpennachlauf": "Pumpennachlauf",
    "anlauf": "Anlauf",
    "overrun": "Nachlauf", "hwc": "Warmwasser", "present": "vorhanden", "missing": "fehlt",
    "heat": "Heizen", "water": "Warmwasser",
}

NUMBER_TYPES = {"UCH", "SCH", "UIN", "SIN", "ULG", "U2L", "D1B", "D1C", "D2B", "D2C", "FLT"}


def _decodable(msg):
    return [f for f in msg.fields if f.name and not f.type.upper().startswith("IGN")]


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _en_name(m, f) -> str:
    """English display name: the message's own English text from `tr`, else its
    technical name; a multi-field entity keeps the field suffix."""
    base = (m.tr or {}).get("en") or m.name
    return _cap(base if f.name == "value" else f"{base} {f.name}")


def _de_name(m, f, key) -> str | None:
    """German display name from `tr`, else the curated NAME_DE override, else None
    (so de.json omits it and HA falls back to the English name)."""
    de = (m.tr or {}).get("de")
    if de:
        return _cap(de if f.name == "value" else f"{de} {f.name}")
    if key in NAME_DE:
        return _cap(NAME_DE[key])
    return None


def build_entities(msgs) -> tuple[dict, dict]:
    """Build the HA ``entity`` sections (English, German) from the definitions,
    taking display names from each definition's ``tr`` (merged from the per-
    language CSVs). German entries are emitted only where a German name or state
    exists, so HA falls back to English for the rest -- which keeps two users on
    one installation each in their own language via HA's own translation system."""
    sensor_en, sensor_de = {}, {}
    number_en, number_de = {}, {}
    select_en, select_de = {}, {}

    for m in msgs:
        if not (m.zz and len(m.zz) == 2):
            continue
        writable = "w" in m.direction
        for f in _decodable(m):
            key = translation_key(m, f)
            en_name, de_name = _en_name(m, f), _de_name(m, f, key)
            states = {_slug(v): v for v in f.values.values()} if f.values else None
            states_de = ({_slug(v): STATE_DE[_slug(v)] for v in f.values.values()
                          if _slug(v) in STATE_DE} if f.values else None)
            if writable and len(_decodable(m)) == 1:
                if f.values:
                    select_en[key] = {"name": en_name, "state": states}
                    de = {**({"name": de_name} if de_name else {}),
                          **({"state": states_de} if states_de else {})}
                    if de:
                        select_de[key] = de
                elif f.type.upper() in NUMBER_TYPES:
                    number_en[key] = {"name": en_name}
                    if de_name:
                        number_de[key] = {"name": de_name}
            elif m.direction[:1] in ("r", "u"):  # updates (broadcasts) are sensors too
                entry = {"name": en_name}
                if states:
                    entry["state"] = states
                sensor_en[key] = entry
                de = {**({"name": de_name} if de_name else {}),
                      **({"state": states_de} if states_de else {})}
                if de:
                    sensor_de[key] = de

    en = {"entity": {"sensor": sensor_en, "number": number_en, "select": select_en}}
    de = {"entity": {"sensor": sensor_de, "number": number_de, "select": select_de}}
    return en, de


def build():
    msgs = []
    for jf in sorted((PKG / "configs" / "vaillant").glob("*.json")):
        if jf.name == "index.json":  # the ident->file map, not a definition file
            continue
        msgs += from_dicts(json.loads(jf.read_text(encoding="utf-8")))

    ent_en, ent_de = build_entities(msgs)
    en = {**CONFIG_EN, **ent_en}
    de = {**CONFIG_DE, **ent_de}
    sensor_en, number_en, select_en = (ent_en["entity"][p] for p in ("sensor", "number", "select"))
    sensor_de, number_de, select_de = (ent_de["entity"][p] for p in ("sensor", "number", "select"))
    (PKG / "strings.json").write_text(json.dumps(en, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (PKG / "translations" / "en.json").write_text(json.dumps(en, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (PKG / "translations" / "de.json").write_text(json.dumps(de, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"sensors={len(sensor_en)} numbers={len(number_en)} selects={len(select_en)}; "
          f"de names={len(sensor_de)+len(number_de)+len(select_de)}")


if __name__ == "__main__":
    build()
