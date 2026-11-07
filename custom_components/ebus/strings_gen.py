"""Build Home Assistant translation files from device definitions.

Shared by the dev tool (``tools/gen_strings.py``) and the runtime generator
(``translate.py``): entity display names come from each definition's ``tr`` (merged from
the per-language CSVs), so the same code produces the English base and any other
language. English always gets a name (the technical name as a last resort); other
languages emit a name only where a translation exists, so Home Assistant falls
back to English per user -- which is how two users on one installation each see
their own language.

No upstream text is stored here: at runtime the ``tr`` comes from the user's own
CDN fetch, and these files are written into the user's install, never this repo.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aioebus.messages import FieldDef, MessageDef
from aioebus.translations import slug, translation_key
from aioebus.values import control_of, values_of

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
            "already_in_progress": "This adapter is already being set up.",
            "no_ebus_data": "The discovered device is not a usable eBUS adapter "
                            "(no eBUS data), or it is in use by another client."},
    },
    "options": {"step": {"init": {
        "title": "eBUS options",
        "data": {"scan_interval": "Base poll interval (s)",
                 "allow_write": "Allow writing settings to the bus",
                 "config_dir": "Extra device-definition directory (optional)",
                 "deep_scan": "Deep scan (probe all bus addresses once)"}}}},
    "services": {"read_register": {
        "name": "Read register",
        "description": "Reads a register of a bus device and returns its raw answer. "
                       "Only reads, changes nothing.",
        "fields": {
            "config_entry_id": {"name": "Adapter", "description": "The eBUS adapter."},
            "address": {"name": "Address",
                        "description": "The device's bus address, two hex digits."},
            "command": {"name": "Command", "description": "PB and SB, four hex digits."},
            "data": {"name": "Data",
                     "description": "What the request carries after the command, in hex."}}}},
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
            "already_in_progress": "Dieser Adapter wird gerade schon eingerichtet.",
            "no_ebus_data": "Das gefundene Gerät ist kein nutzbarer eBUS-Adapter "
                            "(keine eBUS-Daten) oder wird von einem anderen Client belegt."},
    },
    "options": {"step": {"init": {
        "title": "eBUS-Optionen",
        "data": {"scan_interval": "Basis-Abfrageintervall (s)",
                 "allow_write": "Schreiben von Einstellungen auf den Bus erlauben",
                 "config_dir": "Zusätzliches Definitionsverzeichnis (optional)",
                 "deep_scan": "Tiefen-Scan (einmal alle Bus-Adressen prüfen)"}}}},
    "services": {"read_register": {
        "name": "Register lesen",
        "description": "Liest ein Register eines Bus-Geräts und gibt die rohe Antwort "
                       "zurück. Liest nur, ändert nichts.",
        "fields": {
            "config_entry_id": {"name": "Adapter", "description": "Der eBUS-Adapter."},
            "address": {"name": "Adresse",
                        "description": "Bus-Adresse des Geräts, zwei Hex-Ziffern."},
            "command": {"name": "Befehl", "description": "PB und SB, vier Hex-Ziffern."},
            "data": {"name": "Daten",
                     "description": "Was die Anfrage nach dem Befehl mitführt, in Hex."}}}},
}
CONFIG = {"en": CONFIG_EN, "de": CONFIG_DE}

# curated display names per language (names.json): they win over the upstream
# CDN text, which they correct where it misleads; loaded once at import
_CURATED: dict[str, dict[str, dict[str, str]]] = json.loads(
    (Path(__file__).parent / "names.json").read_text(encoding="utf-8"))
CURATED_NAMES = {lang: v["names"] for lang, v in _CURATED.items() if not lang.startswith("_")}
CURATED_STATES = {lang: v["states"] for lang, v in _CURATED.items() if not lang.startswith("_")}

# entities that are no bus register (the adapter link, the identification):
# translation key -> name per language
FIXED_NAMES = {
    "sensor": {
        "adapter_host": {"en": "Host", "de": "Host"},
        "adapter_telegrams": {"en": "Telegrams", "de": "Telegramme"},
        "adapter_polls_answered": {"en": "Polls answered", "de": "Abfragen beantwortet"},
        "adapter_polls_unanswered": {"en": "Polls unanswered", "de": "Abfragen ohne Antwort"},
        "adapter_cached_values": {"en": "Cached values", "de": "Werte zwischengespeichert"},
        "adapter_wifi_signal": {"en": "Wi-Fi signal", "de": "WLAN-Signal"},
        "adapter_uptime": {"en": "Uptime", "de": "Laufzeit"},
        "adapter_free_memory": {"en": "Free memory", "de": "Freier Speicher"},
        "ident_article": {"en": "Article number", "de": "Artikelnummer"},
        "ident_serial": {"en": "Serial number", "de": "Seriennummer"},
        "ident_manufactured": {"en": "Manufactured", "de": "Baujahr"},
        # what applies in heating zone {n} now (zone_state.py)
        "zone_state": {"en": "Zone {n} state", "de": "Zone {n} Zustand"},
        # a heat pump's heat output over its electric power right now
        "cop": {"en": "COP now", "de": "Leistungszahl aktuell"},
    },
    "binary_sensor": {
        "adapter_signal": {"en": "Signal", "de": "Signal"},
    },
    # a heating zone without its own short name; {n} is the zone number
    "climate": {
        "zone": {"en": "Zone {n}", "de": "Zone {n}"},
    },
    "water_heater": {
        "hot_water": {"en": "Hot water", "de": "Warmwasser"},
    },
}

# state labels of their attributes: translation key -> attribute -> language -> labels
FIXED_ATTRIBUTE_STATES = {
    "climate": {
        "zone": {
            "preset_mode": {
                "en": {"none": "None", "away": "Away", "ventilation": "Airing"},
                "de": {"none": "Keine", "away": "Abwesenheit", "ventilation": "Stoßlüften"},
            },
        },
    },
}

# state labels of those entities: translation key -> language -> state -> label
FIXED_STATES = {
    # the hot water's modes as the sensoCOMFORT names them, and its one-time charge
    "water_heater": {
        "hot_water": {
            "en": {"auto": "Time-controlled", "manual": "Manual", "off": "Off",
                   "boost": "One-time charge"},
            "de": {"auto": "Zeitgesteuert", "manual": "Manuell", "off": "Aus",
                   "boost": "Einmal laden"},
        },
    },
    # the thermostat's modes as the sensoCOMFORT names them (auto = time program)
    "climate": {
        "zone": {
            "en": {"auto": "Time-controlled", "heat": "Manual", "off": "Off"},
            "de": {"auto": "Zeitgesteuert", "heat": "Manuell", "off": "Aus"},
        },
    },
    "sensor": {
        "zone_state": {
            "en": {"off": "Off", "manual": "Manual", "time_slot": "Time slot",
                   "setback": "Setback", "eco": "Eco (off, frost protection)",
                   "time_controlled": "Time-controlled", "quick_veto": "Quick veto",
                   "away": "Away", "airing": "Airing (heating off)",
                   "special": "Special function"},
            "de": {"off": "Aus", "manual": "Manuell", "time_slot": "Zeitfenster",
                   "setback": "Absenkung", "eco": "Eco (aus, Frostschutz)",
                   "time_controlled": "Zeitgesteuert", "quick_veto": "Quick-Veto",
                   "away": "Abwesenheit", "airing": "Stoßlüften (Heizung aus)",
                   "special": "Sonderfunktion"},
        },
    },
}


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _good(text: str | None) -> str | None:
    """Keep an upstream comment only if it reads as a short display label, not a
    sentence or a 'Name: description' / value-list blob."""
    t = (text or "").strip()
    return t if t and len(t) <= 40 and ": " not in t else None


def _name(m: MessageDef, f: FieldDef, lang: str) -> str | None:
    """Display name for ``lang``: a curated name, else the upstream label from
    ``tr``, else (English only) the technical name; None means omit so HA falls
    back to English. Curated names win because they correct upstream (e.g. its
    'Wärmeertrag' for what is the ambient yield). A curated name on a multi-field
    entity is the full field label and is used verbatim; the upstream path below
    appends the field name to the message label, which only reads well for a
    lone 'value' field."""
    key = translation_key(m, f)
    curated = CURATED_NAMES.get(lang, {}).get(key)
    if curated and f.name != "value":
        return _cap(curated)
    base = curated or _good((m.tr or {}).get(lang))
    if not base:
        if lang != "en":
            return None
        base = m.name
    return _cap(base if f.name == "value" else f"{base} {f.name}")


def _states(f: FieldDef, lang: str) -> dict[str, str] | None:
    """State names of an enum, keyed by the state itself: Home Assistant looks a
    state up by its value, so a label that is no valid key (e.g. Mo_Fr) cannot
    be translated and is shown as it is."""
    if not f.values:
        return None
    labels = [v for v in dict.fromkeys(f.values.values()) if slug(v) == v]
    if lang == "en":
        return {v: v for v in labels} or None
    table = CURATED_STATES.get(lang, {})
    return {v: table[v] for v in labels if v in table} or None


def build_entities(msgs: list[MessageDef], lang: str) -> dict[str, dict[str, Any]]:
    """The HA ``entity`` sections for one language: the registers' sensors and
    controls (classified like the platforms do, see aioebus.values.control_of) plus the
    fixed adapter/identification entities."""
    base = lang == "en"
    platforms: dict[str, dict[str, Any]] = {
        p: {} for p in ("sensor", "number", "select", "binary_sensor", "climate",
                        "water_heater")}

    def put(target: dict[str, Any], key: str, name: str | None,
            states: dict[str, str] | None) -> None:
        entry = {**({"name": name} if name else {}), **({"state": states} if states else {})}
        if base or entry:  # English always has an entry; others only when non-empty
            target[key] = entry

    for m in msgs:  # the catalog gives every message its device address
        if (control := control_of(m)) is not None:
            kind, value = control
            put(platforms[kind], value.key, _name(m, value.field, lang),
                _states(value.field, lang) if kind == "select" else None)
        # reads and updates (broadcasts) are sensors
        if m.is_read:
            for value in values_of(m):
                put(platforms["sensor"], value.key, _name(m, value.field, lang),
                    _states(value.field, lang))
    for platform, names in FIXED_NAMES.items():
        for key, by_lang in names.items():
            put(platforms[platform], key, by_lang.get(lang),
                FIXED_STATES.get(platform, {}).get(key, {}).get(lang))
            fixed = FIXED_ATTRIBUTE_STATES.get(platform, {}).get(key, {})
            attrs = {attr: {"state": states[lang]} for attr, states in fixed.items()
                     if lang in states}
            if attrs:
                platforms[platform].setdefault(key, {})["state_attributes"] = attrs
    return platforms


def build_strings(msgs: list[MessageDef], lang: str) -> dict[str, Any]:
    """A full HA strings/translations file for one language (UI + entities)."""
    return {**CONFIG.get(lang, {}), "entity": build_entities(msgs, lang)}
