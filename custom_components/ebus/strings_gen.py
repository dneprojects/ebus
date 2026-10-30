"""Build Home Assistant translation files from device definitions.

Shared by the dev tool (``tools/gen_strings.py``) and the runtime generator in the
coordinator: entity display names come from each definition's ``tr`` (merged from
the per-language CSVs), so the same code produces the English base and any other
language. English always gets a name (the technical name as a last resort); other
languages emit a name only where a translation exists, so Home Assistant falls
back to English per user -- which is how two users on one installation each see
their own language.

No upstream text is stored here: at runtime the ``tr`` comes from the user's own
CDN fetch, and these files are written into the user's install, never this repo.
"""
from __future__ import annotations

from aioebus.messages import FieldDef, MessageDef
from aioebus.translations import _slug, translation_key

# config-flow / options UI strings, per language (HA falls back to English for a
# language not listed here)
CONFIG_EN = {
    "config": {
        "flow_title": "eBUS adapter ({host})",
        "step": {
            "user": {
                "title": "eBUS adapter",
                "description": "Connect to an enhanced eBUS adapter (enh:host:port).",
                "data": {"host": "Host", "port": "Port",
                         "allow_write": "Allow writing settings to the bus"}},
            "zeroconf_confirm": {
                "title": "eBUS adapter discovered",
                "description": "An eBUS adapter was detected at {host}. Set it up?",
                "data": {"allow_write": "Allow writing settings to the bus"}}},
        "error": {
            "cannot_connect": "Could not reach the adapter at this host and port.",
            "no_ebus_data": "Reached the adapter, but no eBUS data is coming through. "
                            "Another client (e.g. ebusd) may be using the adapter — "
                            "stop it first, then retry.",
        },
        "abort": {
            "already_configured": "This adapter is already configured.",
            "no_ebus_data": "The discovered device is not a usable eBUS adapter "
                            "(no eBUS data), or it is in use by another client."},
    },
    "options": {"step": {"init": {
        "title": "eBUS options",
        "data": {"scan_interval": "Base poll interval (s)",
                 "allow_write": "Allow writing settings to the bus",
                 "config_dir": "Extra device-definition directory (optional)",
                 "deep_scan": "Deep scan (probe all bus addresses once)"}}}},
}
CONFIG_DE = {
    "config": {
        "flow_title": "eBUS-Adapter ({host})",
        "step": {
            "user": {
                "title": "eBUS-Adapter",
                "description": "Mit einem Enhanced-eBUS-Adapter verbinden (enh:host:port).",
                "data": {"host": "Host", "port": "Port",
                         "allow_write": "Schreiben von Einstellungen auf den Bus erlauben"}},
            "zeroconf_confirm": {
                "title": "eBUS-Adapter gefunden",
                "description": "Ein eBUS-Adapter wurde unter {host} erkannt. Einrichten?",
                "data": {"allow_write": "Schreiben von Einstellungen auf den Bus erlauben"}}},
        "error": {
            "cannot_connect": "Adapter unter diesem Host/Port nicht erreichbar.",
            "no_ebus_data": "Adapter erreichbar, aber es kommen keine eBUS-Daten an. "
                            "Vermutlich nutzt ein anderer Dienst (z. B. ebusd) den "
                            "Adapter — diesen zuerst stoppen und erneut versuchen.",
        },
        "abort": {
            "already_configured": "Dieser Adapter ist bereits eingerichtet.",
            "no_ebus_data": "Das gefundene Gerät ist kein nutzbarer eBUS-Adapter "
                            "(keine eBUS-Daten) oder wird von einem anderen Client belegt."},
    },
    "options": {"step": {"init": {
        "title": "eBUS-Optionen",
        "data": {"scan_interval": "Basis-Abfrageintervall (s)",
                 "allow_write": "Schreiben von Einstellungen auf den Bus erlauben",
                 "config_dir": "Zusätzliches Definitionsverzeichnis (optional)",
                 "deep_scan": "Tiefen-Scan (einmal alle Bus-Adressen prüfen)"}}}},
}
CONFIG = {"en": CONFIG_EN, "de": CONFIG_DE}

# hand-curated overrides (our own work), used when upstream has no good label.
# Only German is curated; other languages rely on the upstream translation alone.
NAME_DE = {
    "flowtemp": "Vorlauftemperatur", "statenumber": "Status", "waterpressure": "Wasserdruck",
    "displayedoutsidetemp": "Außentemperatur", "systemflowtemp": "Anlagen-Vorlauftemperatur",
    "electricpower": "Stromaufnahme Wärmepumpe", "returntemp": "Rücklauftemperatur",
    "hwctemp": "Warmwassertemperatur", "modulation": "Modulation",
    "outsidetemp": "Außentemperatur", "vdatetime": "Datum/Uhrzeit",
    "rundataelectricpowerconsumption": "Stromaufnahme Wärmepumpe",
    "operatinghours": "Betriebsstunden",
    # Status01 is the status telegram the regulator polls continuously (so these
    # arrive passively); its first two temperatures behave as flow and return
    # (the spread opens with the pump). Qualified to not clash with the dedicated
    # FlowTemp / RunDataReturnTemp entities that come from active polls.
    "status01_temp": "Vorlauftemperatur (Status)",
    "status01_temp_1": "Rücklauftemperatur (Status)",
}
STATE_DE = {
    "off": "Aus", "on": "Ein", "auto": "Auto", "yes": "Ja", "no": "Nein",
    "sommer": "Sommer", "winter": "Winter", "heizen": "Heizen", "warmwasser": "Warmwasser",
    "kein_bedarf": "Kein Bedarf", "frostschutz": "Frostschutz",
    "heat": "Heizen", "water": "Warmwasser", "day": "Tag", "night": "Nacht",
}
CURATED_NAMES = {"de": NAME_DE}
CURATED_STATES = {"de": STATE_DE}

NUMBER_TYPES = frozenset(
    {"UCH", "SCH", "UIN", "SIN", "ULG", "U2L", "D1B", "D1C", "D2B", "D2C", "FLT"})


def _decodable(msg: MessageDef) -> list[FieldDef]:
    return [f for f in msg.fields if f.name and not f.type.upper().startswith("IGN")]


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _good(text) -> str | None:
    """Keep an upstream comment only if it reads as a short display label, not a
    sentence or a 'Name: description' / value-list blob."""
    t = (text or "").strip()
    return t if t and len(t) <= 40 and ": " not in t else None


def _name(m: MessageDef, f: FieldDef, lang: str) -> str | None:
    """Display name for ``lang``: upstream label from ``tr``, else a curated
    override, else (English only) the technical name; None means omit so HA falls
    back to English. A curated override on a multi-field entity is the full field
    label and is used verbatim; the upstream path below appends the field name to
    the message label, which only reads well for a lone 'value' field."""
    key = translation_key(m, f)
    curated = CURATED_NAMES.get(lang, {}).get(key)
    if curated and f.name != "value":
        return _cap(curated)
    base = _good((m.tr or {}).get(lang)) or curated
    if not base:
        if lang != "en":
            return None
        base = m.name
    return _cap(base if f.name == "value" else f"{base} {f.name}")


def _states(f: FieldDef, lang: str) -> dict | None:
    if not f.values:
        return None
    if lang == "en":
        return {_slug(v): v for v in f.values.values()}
    table = CURATED_STATES.get(lang, {})
    out = {_slug(v): table[_slug(v)] for v in f.values.values() if _slug(v) in table}
    return out or None


def build_entities(msgs: list[MessageDef], lang: str) -> dict:
    """The HA ``entity`` sections (sensor/number/select) for one language."""
    base = lang == "en"
    sensor: dict = {}
    number: dict = {}
    select: dict = {}

    def put(target: dict, key: str, name: str | None, states: dict | None) -> None:
        entry = {**({"name": name} if name else {}), **({"state": states} if states else {})}
        if base or entry:  # English always has an entry; others only when non-empty
            target[key] = entry

    for m in msgs:
        if not (m.zz and len(m.zz) == 2):
            continue
        decs = _decodable(m)
        writable = "w" in m.direction
        for f in decs:
            key = translation_key(m, f)
            name, states = _name(m, f, lang), _states(f, lang)
            if writable and len(decs) == 1:
                if f.values:
                    put(select, key, name, states)
                elif f.type.upper() in NUMBER_TYPES:
                    put(number, key, name, None)
            elif m.direction[:1] in ("r", "u"):  # updates (broadcasts) are sensors too
                put(sensor, key, name, states)
    return {"sensor": sensor, "number": number, "select": select}


def build_strings(msgs: list[MessageDef], lang: str) -> dict:
    """A full HA strings/translations file for one language (UI + entities)."""
    return {**CONFIG.get(lang, {}), "entity": build_entities(msgs, lang)}
