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


def _primary(msg):
    return next((f for f in msg.fields if f.name and not f.type.upper().startswith("IGN")), None)


def _decodable(msg):
    return [f for f in msg.fields if f.name and not f.type.upper().startswith("IGN")]


def build():
    msgs = []
    for jf in sorted((PKG / "configs" / "vaillant").glob("*.json")):
        if jf.name == "index.json":  # the ident->file map, not a definition file
            continue
        msgs += from_dicts(json.loads(jf.read_text(encoding="utf-8")))

    sensor_en, sensor_de = {}, {}
    number_en, number_de = {}, {}
    select_en, select_de = {}, {}

    for m in msgs:
        if not (m.zz and len(m.zz) == 2):
            continue
        writable = "w" in m.direction
        for f in _decodable(m):
            key = translation_key(m, f)
            name = m.name if f.name == "value" else f"{m.name} {f.name}"
            states = {_slug(v): v for v in f.values.values()} if f.values else None
            states_de = ({_slug(v): STATE_DE[_slug(v)] for v in f.values.values()
                          if _slug(v) in STATE_DE} if f.values else None)
            if writable and len(_decodable(m)) == 1:
                if f.values:
                    select_en[key] = {"name": name, "state": states}
                    if states_de:
                        select_de[key] = {"name": NAME_DE.get(key, name), "state": states_de}
                    elif key in NAME_DE:
                        select_de[key] = {"name": NAME_DE[key]}
                elif f.type.upper() in NUMBER_TYPES:
                    number_en[key] = {"name": name}
                    if key in NAME_DE:
                        number_de[key] = {"name": NAME_DE[key]}
            elif m.direction[:1] in ("r", "u"):  # updates (broadcasts) are sensors too
                entry = {"name": name}
                if states:
                    entry["state"] = states
                sensor_en[key] = entry
                de = {}
                if key in NAME_DE:
                    de["name"] = NAME_DE[key]
                if states_de:
                    de["state"] = states_de
                if de:
                    sensor_de[key] = de

    en = {**CONFIG_EN, "entity": {"sensor": sensor_en, "number": number_en, "select": select_en}}
    de = {**CONFIG_DE, "entity": {"sensor": sensor_de, "number": number_de, "select": select_de}}
    (PKG / "strings.json").write_text(json.dumps(en, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (PKG / "translations" / "en.json").write_text(json.dumps(en, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (PKG / "translations" / "de.json").write_text(json.dumps(de, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"sensors={len(sensor_en)} numbers={len(number_en)} selects={len(select_en)}; "
          f"de names={len(sensor_de)+len(number_de)+len(select_de)}")


if __name__ == "__main__":
    build()
