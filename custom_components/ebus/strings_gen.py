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

# hand-curated names (our own work, reviewed per key); they win over the upstream
# label. Only German is curated; other languages rely on the upstream translation.
# Keyed by translation key, grouped by the device the reference install has it on.
# Semantics worth knowing: Status01 (b511 01) slots are flow, return, outside,
# DHW, storage -- "(Status)" keeps them apart from the actively polled entities;
# b516/10 counters: environment = ambient yield (Vaillant's "Yield"), generated =
# delivered heat, electric = input; b51a Day/Month figures are ambient yield too.
NAME_DE = {
    # aguaFLOW
    "actorfernsteuerung": "Fernsteuerung Aktoren",
    "actualtappingtempdesired": "Zapf-Sollwert aktuell",
    "autotappingtempdesired": "Zapf-Sollwert Automatik",
    "calculatedmixerposition": "Mischer Stellung berechnet",
    "circopump": "Zirkulationspumpe",
    "electricalheater": "Elektro-Heizstab",
    "enablehwcoperation": "Warmwasserbetrieb freigegeben",
    "heatdemand": "Wärmeanforderung",
    "hexindex": "Wärmetauscher Verkalkungsindex",
    "hexmonitoringcounter": "Wärmetauscher Überwachungszähler",
    "hexthreshold": "Wärmetauscher Verkalkungsschwelle",
    "holidaystart": "Urlaub aktiv",
    "krm": "Regler Mischer Kr",
    "krmfaktormiddl": "Regler Mischer Kr-Faktor mittel",
    "krmfaktorsmall": "Regler Mischer Kr-Faktor klein",
    "krp": "Regler Pumpe Kr",
    "legiostartday": "Legionellenschutz Tag",
    "legiostarttime": "Legionellenschutz Uhrzeit",
    "load": "Speicherladung",
    "manualtappingtempdesired": "Zapf-Sollwert manuell",
    "mode_daynight": "Betriebsart Tag/Nacht",
    "mode_hex": "Betriebsart Rohwert",
    "mode_hwcmode": "Betriebsart Warmwasser",
    "mode_hwcmode_1": "Betriebsart Warmwasser 2",
    "mode_mctype": "Betriebsart Kreistyp",
    "mode_temp": "Betriebsart Temperatur",
    "ntc1sensor_sensor": "Temperatursensor 1 Status",
    "ntc1sensor_temp": "Temperatursensor 1",
    "ntc2sensor_sensor": "Temperatursensor 2 Status",
    "ntc2sensor_temp": "Temperatursensor 2",
    "ntc3sensor_sensor": "Temperatursensor 3 Status",
    "ntc3sensor_temp": "Temperatursensor 3",
    "operatinghours": "Betriebsstunden",
    "operatingmode": "Betriebsart",
    "party": "Party",
    "partystart": "Party aktiv",
    "pumped": "Pumpe Einschaltdauer",
    "quickvetostart": "Quick-Veto aktiv",
    "quickvetotempdesired": "Quick-Veto Sollwert",
    "setmode": "Betriebsvorgabe",
    "settempdesired": "Sollwert setzen",
    "singlestoragecharge": "Einmalige Speicherladung",
    "statecircokick": "Zirkulation Anstoß",
    "statehexmonitoring": "Wärmetauscher Überwachung",
    "statemaincontrol": "Hauptregelung Zustand",
    "statemixerpumpcontrol": "Mischer/Pumpe Zustand",
    "status_onoff": "Ein/Aus (Status)",
    "status_temp": "Sollwert (Status)",
    "status_temp_1": "Istwert (Status)",
    "status_temp_2": "Sollwert 2 (Status)",
    "stfrostprotcounter": "Frostschutz Zähler",
    "stlegioaborttimecounter": "Legionellenschutz Abbruchzähler",
    "stlegiostoptimecounter": "Legionellenschutz Stoppzähler",
    "teleswitch": "Fernschalter",
    "teleswitchstate": "Fernschalter Zustand",
    "timeticker": "Betriebszeit Zähler",
    "tm": "Regler Mischer Tm",
    "tnm": "Regler Mischer Nachstellzeit",
    "tnmfaktormiddl": "Regler Mischer Nachstellzeit-Faktor mittel",
    "tnmfaktorsmall": "Regler Mischer Nachstellzeit-Faktor klein",
    "tp": "Regler Pumpe Tp",
    "tvm": "Regler Mischer Vorhaltezeit",
    "tvp": "Regler Pumpe Vorhaltezeit",
    "vpmwmode": "Stationsmodus",
    "weekday": "Wochentag",
    # aguaFLOW, aroTHERM plus 0, aroTHERM plus 1, eloBlock
    "flowtemp": "Vorlauftemperatur",
    # aguaFLOW, sensoCOMFORT
    "date": "Datum",
    "time": "Uhrzeit",
    # aroTHERM plus 0
    "compressorhc_cycles": "Verdichter Heizen Starts",
    "compressorhc_runtime": "Verdichter Heizen Laufzeit",
    "compressorhwc_cycles": "Verdichter Warmwasser Starts",
    "compressorhwc_runtime": "Verdichter Warmwasser Laufzeit",
    # aroTHERM plus 0, aroTHERM plus 1
    "buildingcircuitflow": "Heizkreis Volumenstrom",
    "buildingpumphours": "Heizkreispumpe Betriebsstunden",
    "buildingpumpstarts": "Heizkreispumpe Starts",
    "compressorblocktime": "Verdichter Sperrzeit",
    "compressorhours": "Verdichter Betriebsstunden",
    "compressorstarts": "Verdichter Starts",
    "copcooling": "Arbeitszahl Kühlen",
    "copcoolingmonth": "Arbeitszahl Kühlen Monat",
    "cophc": "Arbeitszahl Heizen",
    "cophcmonth": "Arbeitszahl Heizen Monat",
    "cophwc": "Arbeitszahl Warmwasser",
    "cophwcmonth": "Arbeitszahl Warmwasser Monat",
    "currentcompressorutil": "Verdichter Auslastung",
    "currentyieldpower": "Umweltleistung",
    "datetime_bdate": "Datum (WP)",
    "datetime_btime": "Uhrzeit (WP)",
    "datetime_dcfstate": "Funkuhr Status",
    "datetime_temp": "Außentemperatur (WP)",
    "energyelectriccool": "Stromverbrauch Kühlen",
    "energyelectrichc": "Stromverbrauch Heizen",
    "energyelectrichwc": "Stromverbrauch Warmwasser",
    "energyenvironmentcool": "Umweltertrag Kühlen",
    "energyenvironmenthc": "Umweltertrag Heizen",
    "energyenvironmenthwc": "Umweltertrag Warmwasser",
    "energygeneratedcool": "Kälteabgabe Kühlen",
    "energygeneratedhc": "Wärmeabgabe Heizen",
    "energygeneratedhwc": "Wärmeabgabe Warmwasser",
    "energyintegral": "Energieintegral",
    "fan1hours": "Ventilator 1 Betriebsstunden",
    "fan1starts": "Ventilator 1 Starts",
    "fan2hours": "Ventilator 2 Betriebsstunden",
    "fan2starts": "Ventilator 2 Starts",
    "flowpressure": "Anlagendruck",
    "fourwayvalvehours": "4-Wege-Ventil Betriebsstunden",
    "fourwayvalveswitches": "4-Wege-Ventil Umschaltungen",
    "hours": "Betriebsstunden",
    "hourscool": "Betriebsstunden Kühlen",
    "hourshc": "Betriebsstunden Heizen",
    "hourshwc": "Betriebsstunden Warmwasser",
    "powerconsumptionhmu": "Stromaufnahme Wärmepumpe",
    "rundataairinlettemp": "Lufteintrittstemperatur",
    "rundatabuildingcpumppower": "Heizkreispumpe Leistung",
    "rundatacompressorinlettemp": "Verdichter Eintrittstemperatur",
    "rundatacompressoroutlettemp": "Verdichter Austrittstemperatur",
    "rundatacompressorspeed": "Verdichter Drehzahl",
    "rundataeevoutlettemp": "Expansionsventil Austrittstemperatur",
    "rundataeevpositionabs": "Expansionsventil Position",
    "rundatafan1speed": "Ventilator 1 Drehzahl",
    "rundatafan2speed": "Ventilator 2 Drehzahl",
    "rundataflowtemp": "Vorlauftemperatur (Betrieb)",
    "rundatahighpressure": "Hochdruck",
    "rundatareturntemp": "Rücklauftemperatur",
    "rundatastatuscode": "Statuscode",
    "runstats4portvalvehours": "4-Wege-Ventil Betriebsstunden",
    "runstats4portvalveswitches": "4-Wege-Ventil Umschaltungen",
    "runstatsbuildingcpumpstarts": "Heizkreispumpe Starts",
    "runstatsbuildingpumphours": "Heizkreispumpe Betriebsstunden",
    "runstatscompressorhours": "Verdichter Betriebsstunden",
    "runstatscompressorstarts": "Verdichter Starts",
    "runstatsfan1hours": "Ventilator 1 Betriebsstunden",
    "runstatsfan1starts": "Ventilator 1 Starts",
    "runstatsfan2hours": "Ventilator 2 Betriebsstunden",
    "runstatsfan2starts": "Ventilator 2 Starts",
    "runstatshchours": "Betriebsstunden Heizen",
    "runstatshmuhours": "Betriebsstunden Regelung",
    "runstatshwchours": "Betriebsstunden Warmwasser",
    "setmode_disablehc": "Vorgabe Heizen gesperrt",
    "setmode_disablehwcload": "Vorgabe Speicherladung gesperrt",
    "setmode_disablehwctapping": "Vorgabe Warmwasserzapfung gesperrt",
    "setmode_flowtempdesired": "Vorgabe Vorlauf-Sollwert",
    "setmode_hcmode": "Vorgabe Heizbetrieb",
    "setmode_hwcflowtempdesired": "Vorgabe Warmwasser-Vorlauf-Sollwert",
    "setmode_hwctempdesired": "Vorgabe Warmwasser-Sollwert",
    "setmode_releasebackup": "Vorgabe Zuheizer freigegeben",
    "setmode_releasecooling": "Vorgabe Kühlen freigegeben",
    "setmode_remotecontrolhcpump": "Vorgabe Heizkreispumpe fernsteuern",
    "state_energy": "Energiestatus",
    "state_energy_1": "Energiezähler (Status)",
    "state_onoff": "Ein/Aus",
    "state_state": "Betriebszustand",
    "status01_pumpstate": "Pumpenstatus (Status)",
    "status01_temp": "Vorlauftemperatur (Status)",
    "status01_temp_1": "Rücklauftemperatur (Status)",
    "status01_temp_2": "Außentemperatur (Status)",
    "status01_temp_3": "Warmwassertemperatur (Status)",
    "status01_temp_4": "Speichertemperatur (Status)",
    "statuscirpump": "Zirkulationspumpe",
    "targetflowtemp": "Vorlauf-Sollwert",
    "yieldcoolday": "Umweltertrag Kühlen heute",
    "yieldcoolingmonth": "Umweltertrag Kühlen Monat",
    "yieldhcday": "Umweltertrag Heizen heute",
    "yieldhcmonth": "Umweltertrag Heizen Monat",
    "yieldhwcday": "Umweltertrag Warmwasser heute",
    "yieldhwcmonth": "Umweltertrag Warmwasser Monat",
    # aroTHERM plus 1
    "runstatscompressorhc_cycles": "Verdichter Heizen Starts",
    "runstatscompressorhc_runtime": "Verdichter Heizen Laufzeit",
    "runstatscompressorhwc_cycles": "Verdichter Warmwasser Starts",
    "runstatscompressorhwc_runtime": "Verdichter Warmwasser Laufzeit",
    # eBUS Adapter
    "outsidetemp": "Außentemperatur",
    "vdatetime_date": "Datum (Bus)",
    "vdatetime_time": "Uhrzeit (Bus)",
    # eloBlock
    "anticondensvalue": "Kondensationsschutz Leistung",
    "blocktimehcmax": "Heizen Sperrzeit max.",
    "boilertype": "Gerätetyp",
    "cirpump": "Zirkulationspumpe",
    "dsn": "Gerätekennung (DSN)",
    "ebusheatcontrol": "Regelung über eBUS",
    "ebusvoltage": "eBUS-Speisung",
    "flowtempdemand": "Vorlauf-Anforderung",
    "hcpumpmode": "Heizkreispumpe Betriebsart",
    "heatingdemand": "Heizanforderung",
    "heatingswitch": "Heizen freigegeben",
    "hwcdemand": "Warmwasseranforderung",
    "hwcswitch": "Warmwasser freigegeben",
    "hwctemp": "Warmwassertemperatur",
    "hwctempmax": "Warmwasser Sollwert max.",
    "modulationdesired": "Modulation Soll",
    "partloadhckw": "Teillast Heizen",
    "partloadhwckw": "Teillast Warmwasser",
    "positionvalveset": "Umschaltventil Stellung",
    "powervalue": "Leistungswerte (roh)",
    "probereg2800": "Register 0d2800 (unbekannt)",
    "probereg3703": "Register 0d3703 (unbekannt)",
    "probereg3803": "Register 0d3803 (unbekannt)",
    "probereg3a03": "Register 0d3a03 (unbekannt)",
    "probereg4003": "Register 0d4003 (unbekannt)",
    "pumppower": "Leistung Pumpe",
    "pumpstate": "Status Pumpe",
    "statenumber": "Status",
    "storageloadpump": "Speicherladepumpe",
    "templimiter": "Sicherheitstemperaturbegrenzer",
    "templimitercutoffs": "Sicherheitstemperaturbegrenzer Auslösungen",
    "warmstartenable": "Warmstart",
    "wp": "Wärmepumpe angeschlossen",
    # eloBlock, sensoCOMFORT
    "waterpressure": "Wasserdruck",
    # sensoCOMFORT
    "adaptheatcurve": "Adaptive Heizkurve",
    "cctimer_friday0_htm": "Zirkulation Zeitfenster Freitag Beginn",
    "cctimer_friday0_htm_1": "Zirkulation Zeitfenster Freitag Ende",
    "cctimer_monday0_htm": "Zirkulation Zeitfenster Montag Beginn",
    "cctimer_monday0_htm_1": "Zirkulation Zeitfenster Montag Ende",
    "cctimer_saturday0_htm": "Zirkulation Zeitfenster Samstag Beginn",
    "cctimer_saturday0_htm_1": "Zirkulation Zeitfenster Samstag Ende",
    "cctimer_sunday0_htm": "Zirkulation Zeitfenster Sonntag Beginn",
    "cctimer_sunday0_htm_1": "Zirkulation Zeitfenster Sonntag Ende",
    "cctimer_thursday0_htm": "Zirkulation Zeitfenster Donnerstag Beginn",
    "cctimer_thursday0_htm_1": "Zirkulation Zeitfenster Donnerstag Ende",
    "cctimer_timeframes_slotcount": "Zirkulation Zeitfenster Anzahl Montag",
    "cctimer_timeframes_slotcount_1": "Zirkulation Zeitfenster Anzahl Dienstag",
    "cctimer_timeframes_slotcount_2": "Zirkulation Zeitfenster Anzahl Mittwoch",
    "cctimer_timeframes_slotcount_3": "Zirkulation Zeitfenster Anzahl Donnerstag",
    "cctimer_timeframes_slotcount_4": "Zirkulation Zeitfenster Anzahl Freitag",
    "cctimer_timeframes_slotcount_5": "Zirkulation Zeitfenster Anzahl Samstag",
    "cctimer_timeframes_slotcount_6": "Zirkulation Zeitfenster Anzahl Sonntag",
    "cctimer_tuesday0_htm": "Zirkulation Zeitfenster Dienstag Beginn",
    "cctimer_tuesday0_htm_1": "Zirkulation Zeitfenster Dienstag Ende",
    "cctimer_wednesday0_htm": "Zirkulation Zeitfenster Mittwoch Beginn",
    "cctimer_wednesday0_htm_1": "Zirkulation Zeitfenster Mittwoch Ende",
    "continuousheating": "Durchheizen unter Außentemperatur",
    "cylinderchargehyst": "Speicherladung Hysterese",
    "cylinderchargeoffset": "Speicherladung Überhöhung",
    "displayedoutsidetemp": "Außentemperatur",
    "frostoverridetime": "Frostschutz-Verzögerung",
    "hc1actualflowtempdesired": "Heizkreis 1 Vorlauf-Sollwert aktuell",
    "hc1autooffmode": "Heizkreis 1 Verhalten außerhalb Zeitfenster",
    "hc1circuittype_mctype": "Heizkreis 1 Kreistyp",
    "hc1excesstemp": "Heizkreis 1 Vorlauf-Überhöhung",
    "hc1flowtemp": "Heizkreis 1 Vorlauftemperatur",
    "hc1heatcurve": "Heizkreis 1 Heizkurve",
    "hc1heatcurveadaption": "Heizkreis 1 Heizkurven-Anpassung",
    "hc1maxflowtempdesired": "Heizkreis 1 Vorlauf-Sollwert max.",
    "hc1mincoolingtempdesired": "Heizkreis 1 Kühl-Vorlauf min.",
    "hc1minflowtempdesired": "Heizkreis 1 Vorlauf-Sollwert min.",
    "hc1mixermovement": "Heizkreis 1 Mischerbewegung",
    "hc1pumpstatus": "Heizkreis 1 Pumpe",
    "hc1roomtempswitchon": "Heizkreis 1 Raumaufschaltung",
    "hc1status": "Heizkreis 1 Status",
    "hc1summertemplimit": "Heizkreis 1 AT-Abschaltgrenze",
    "hc2actualflowtempdesired": "Heizkreis 2 Vorlauf-Sollwert aktuell",
    "hc2autooffmode": "Heizkreis 2 Verhalten außerhalb Zeitfenster",
    "hc2circuittype_mctype": "Heizkreis 2 Kreistyp",
    "hc2excesstemp": "Heizkreis 2 Vorlauf-Überhöhung",
    "hc2flowtemp": "Heizkreis 2 Vorlauftemperatur",
    "hc2heatcurve": "Heizkreis 2 Heizkurve",
    "hc2heatcurveadaption": "Heizkreis 2 Heizkurven-Anpassung",
    "hc2maxflowtempdesired": "Heizkreis 2 Vorlauf-Sollwert max.",
    "hc2mincoolingtempdesired": "Heizkreis 2 Kühl-Vorlauf min.",
    "hc2minflowtempdesired": "Heizkreis 2 Vorlauf-Sollwert min.",
    "hc2mixermovement": "Heizkreis 2 Mischerbewegung",
    "hc2pumpstatus": "Heizkreis 2 Pumpe",
    "hc2roomtempswitchon": "Heizkreis 2 Raumaufschaltung",
    "hc2status": "Heizkreis 2 Status",
    "hc2summertemplimit": "Heizkreis 2 AT-Abschaltgrenze",
    "hc3actualflowtempdesired": "Heizkreis 3 Vorlauf-Sollwert aktuell",
    "hc3autooffmode": "Heizkreis 3 Verhalten außerhalb Zeitfenster",
    "hc3circuittype_mctype": "Heizkreis 3 Kreistyp",
    "hc3excesstemp": "Heizkreis 3 Vorlauf-Überhöhung",
    "hc3heatcurve": "Heizkreis 3 Heizkurve",
    "hc3heatcurveadaption": "Heizkreis 3 Heizkurven-Anpassung",
    "hc3maxflowtempdesired": "Heizkreis 3 Vorlauf-Sollwert max.",
    "hc3mincoolingtempdesired": "Heizkreis 3 Kühl-Vorlauf min.",
    "hc3minflowtempdesired": "Heizkreis 3 Vorlauf-Sollwert min.",
    "hc3mixermovement": "Heizkreis 3 Mischerbewegung",
    "hc3pumpstatus": "Heizkreis 3 Pumpe",
    "hc3roomtempswitchon": "Heizkreis 3 Raumaufschaltung",
    "hc3status": "Heizkreis 3 Status",
    "hc3summertemplimit": "Heizkreis 3 AT-Abschaltgrenze",
    "hwcflowtemp": "Warmwasser Vorlauftemperatur",
    "hwcholidayendperiod": "Warmwasser Urlaub Ende",
    "hwcholidaystartperiod": "Warmwasser Urlaub Beginn",
    "hwclegionelladay": "Legionellenschutz Tag",
    "hwclegionellatime": "Legionellenschutz Uhrzeit",
    "hwclocktime": "Warmwasser Sperrzeit",
    "hwcmaxflowtempdesired": "Warmwasser Vorlauf-Sollwert max.",
    "hwcopmode": "Warmwasser Betriebsart",
    "hwcparallelloading": "Warmwasser Parallelladung",
    "hwcsfmode": "Warmwasser Sonderbetrieb",
    "hwcstoragetemp": "Warmwasser Speichertemperatur",
    "hwctempdesired": "Warmwasser Sollwert",
    "hwctimer_friday0_htm": "Warmwasser Zeitfenster Freitag Beginn",
    "hwctimer_friday0_htm_1": "Warmwasser Zeitfenster Freitag Ende",
    "hwctimer_friday0_slottemp": "Warmwasser Zeitfenster Freitag Temperatur",
    "hwctimer_monday0_htm": "Warmwasser Zeitfenster Montag Beginn",
    "hwctimer_monday0_htm_1": "Warmwasser Zeitfenster Montag Ende",
    "hwctimer_monday0_slottemp": "Warmwasser Zeitfenster Montag Temperatur",
    "hwctimer_saturday0_htm": "Warmwasser Zeitfenster Samstag Beginn",
    "hwctimer_saturday0_htm_1": "Warmwasser Zeitfenster Samstag Ende",
    "hwctimer_saturday0_slottemp": "Warmwasser Zeitfenster Samstag Temperatur",
    "hwctimer_sunday0_htm": "Warmwasser Zeitfenster Sonntag Beginn",
    "hwctimer_sunday0_htm_1": "Warmwasser Zeitfenster Sonntag Ende",
    "hwctimer_sunday0_slottemp": "Warmwasser Zeitfenster Sonntag Temperatur",
    "hwctimer_thursday0_htm": "Warmwasser Zeitfenster Donnerstag Beginn",
    "hwctimer_thursday0_htm_1": "Warmwasser Zeitfenster Donnerstag Ende",
    "hwctimer_thursday0_slottemp": "Warmwasser Zeitfenster Donnerstag Temperatur",
    "hwctimer_timeframes_slotcount": "Warmwasser Zeitfenster Anzahl Montag",
    "hwctimer_timeframes_slotcount_1": "Warmwasser Zeitfenster Anzahl Dienstag",
    "hwctimer_timeframes_slotcount_2": "Warmwasser Zeitfenster Anzahl Mittwoch",
    "hwctimer_timeframes_slotcount_3": "Warmwasser Zeitfenster Anzahl Donnerstag",
    "hwctimer_timeframes_slotcount_4": "Warmwasser Zeitfenster Anzahl Freitag",
    "hwctimer_timeframes_slotcount_5": "Warmwasser Zeitfenster Anzahl Samstag",
    "hwctimer_timeframes_slotcount_6": "Warmwasser Zeitfenster Anzahl Sonntag",
    "hwctimer_tuesday0_htm": "Warmwasser Zeitfenster Dienstag Beginn",
    "hwctimer_tuesday0_htm_1": "Warmwasser Zeitfenster Dienstag Ende",
    "hwctimer_tuesday0_slottemp": "Warmwasser Zeitfenster Dienstag Temperatur",
    "hwctimer_wednesday0_htm": "Warmwasser Zeitfenster Mittwoch Beginn",
    "hwctimer_wednesday0_htm_1": "Warmwasser Zeitfenster Mittwoch Ende",
    "hwctimer_wednesday0_slottemp": "Warmwasser Zeitfenster Mittwoch Temperatur",
    "hydraulicscheme": "Hydraulikschema",
    "keycodeforconfigmenu": "Code Fachhandwerkerebene",
    "maintenancedate": "Nächste Wartung",
    "maintenancedue": "Wartung fällig",
    "maxcylinderchargetime": "Speicherladung max. Dauer",
    "maxroomhumidity": "Raumfeuchte max.",
    "multirelaysetting": "Multifunktionsausgang",
    "outsidetempavg": "Außentemperatur Mittelwert",
    "prenergysumhc": "Energie Heizen gesamt",
    "prenergysumhclastmonth": "Energie Heizen Vormonat",
    "prenergysumhcthismonth": "Energie Heizen dieser Monat",
    "prenergysumhwc": "Energie Warmwasser gesamt",
    "prenergysumhwclastmonth": "Energie Warmwasser Vormonat",
    "prenergysumhwcthismonth": "Energie Warmwasser dieser Monat",
    "prfuelsumhc": "Brennstoff Heizen gesamt",
    "prfuelsumhclastmonth": "Brennstoff Heizen Vormonat",
    "prfuelsumhcthismonth": "Brennstoff Heizen dieser Monat",
    "prfuelsumhwc": "Brennstoff Warmwasser gesamt",
    "prfuelsumhwclastmonth": "Brennstoff Warmwasser Vormonat",
    "prfuelsumhwcthismonth": "Brennstoff Warmwasser dieser Monat",
    "pumpadditionaltime": "Pumpennachlaufzeit",
    "solaryieldtotal": "Solarertrag gesamt",
    "systemflowtemp": "Anlagen-Vorlauftemperatur",
    "yieldtotal": "Umweltertrag gesamt",
    "z1actualroomtempdesired": "Zone 1 Raum-Sollwert aktuell",
    "z1coolingtemp": "Zone 1 Kühl-Sollwert",
    "z1daytemp": "Zone 1 Tag-Sollwert",
    "z1holidayendperiod": "Zone 1 Urlaub Ende",
    "z1holidaystartperiod": "Zone 1 Urlaub Beginn",
    "z1holidaytemp": "Zone 1 Urlaubs-Sollwert",
    "z1name1": "Zone 1 Name Teil 1",
    "z1name2": "Zone 1 Name Teil 2",
    "z1nighttemp": "Zone 1 Absenk-Sollwert",
    "z1opmode": "Zone 1 Betriebsart",
    "z1quickvetoduration": "Zone 1 Quick-Veto Dauer",
    "z1quickvetoenddate": "Zone 1 Quick-Veto Ende Datum",
    "z1quickvetoendtime": "Zone 1 Quick-Veto Ende Uhrzeit",
    "z1quickvetotemp": "Zone 1 Quick-Veto Sollwert",
    "z1roomtemp": "Zone 1 Raumtemperatur",
    "z1roomzonemapping": "Zone 1 Raumtemperatursensor-Zuordnung",
    "z1sfmode": "Zone 1 Sonderbetrieb",
    "z1shortname": "Zone 1 Kurzname",
    "z1timer_friday0_htm": "Zone 1 Zeitfenster Freitag Beginn",
    "z1timer_friday0_htm_1": "Zone 1 Zeitfenster Freitag Ende",
    "z1timer_friday0_slottemp": "Zone 1 Zeitfenster Freitag Temperatur",
    "z1timer_monday0_htm": "Zone 1 Zeitfenster Montag Beginn",
    "z1timer_monday0_htm_1": "Zone 1 Zeitfenster Montag Ende",
    "z1timer_monday0_slottemp": "Zone 1 Zeitfenster Montag Temperatur",
    "z1timer_saturday0_htm": "Zone 1 Zeitfenster Samstag Beginn",
    "z1timer_saturday0_htm_1": "Zone 1 Zeitfenster Samstag Ende",
    "z1timer_saturday0_slottemp": "Zone 1 Zeitfenster Samstag Temperatur",
    "z1timer_sunday0_htm": "Zone 1 Zeitfenster Sonntag Beginn",
    "z1timer_sunday0_htm_1": "Zone 1 Zeitfenster Sonntag Ende",
    "z1timer_sunday0_slottemp": "Zone 1 Zeitfenster Sonntag Temperatur",
    "z1timer_thursday0_htm": "Zone 1 Zeitfenster Donnerstag Beginn",
    "z1timer_thursday0_htm_1": "Zone 1 Zeitfenster Donnerstag Ende",
    "z1timer_thursday0_slottemp": "Zone 1 Zeitfenster Donnerstag Temperatur",
    "z1timer_timeframes_slotcount": "Zone 1 Zeitfenster Anzahl Montag",
    "z1timer_timeframes_slotcount_1": "Zone 1 Zeitfenster Anzahl Dienstag",
    "z1timer_timeframes_slotcount_2": "Zone 1 Zeitfenster Anzahl Mittwoch",
    "z1timer_timeframes_slotcount_3": "Zone 1 Zeitfenster Anzahl Donnerstag",
    "z1timer_timeframes_slotcount_4": "Zone 1 Zeitfenster Anzahl Freitag",
    "z1timer_timeframes_slotcount_5": "Zone 1 Zeitfenster Anzahl Samstag",
    "z1timer_timeframes_slotcount_6": "Zone 1 Zeitfenster Anzahl Sonntag",
    "z1timer_tuesday0_htm": "Zone 1 Zeitfenster Dienstag Beginn",
    "z1timer_tuesday0_htm_1": "Zone 1 Zeitfenster Dienstag Ende",
    "z1timer_tuesday0_slottemp": "Zone 1 Zeitfenster Dienstag Temperatur",
    "z1timer_wednesday0_htm": "Zone 1 Zeitfenster Mittwoch Beginn",
    "z1timer_wednesday0_htm_1": "Zone 1 Zeitfenster Mittwoch Ende",
    "z1timer_wednesday0_slottemp": "Zone 1 Zeitfenster Mittwoch Temperatur",
    "z1valvestatus": "Zone 1 Ventilstatus",
    "z2actualroomtempdesired": "Zone 2 Raum-Sollwert aktuell",
    "z2coolingtemp": "Zone 2 Kühl-Sollwert",
    "z2daytemp": "Zone 2 Tag-Sollwert",
    "z2holidayendperiod": "Zone 2 Urlaub Ende",
    "z2holidaystartperiod": "Zone 2 Urlaub Beginn",
    "z2holidaytemp": "Zone 2 Urlaubs-Sollwert",
    "z2name1": "Zone 2 Name Teil 1",
    "z2name2": "Zone 2 Name Teil 2",
    "z2nighttemp": "Zone 2 Absenk-Sollwert",
    "z2opmode": "Zone 2 Betriebsart",
    "z2quickvetoduration": "Zone 2 Quick-Veto Dauer",
    "z2quickvetoenddate": "Zone 2 Quick-Veto Ende Datum",
    "z2quickvetoendtime": "Zone 2 Quick-Veto Ende Uhrzeit",
    "z2quickvetotemp": "Zone 2 Quick-Veto Sollwert",
    "z2roomtemp": "Zone 2 Raumtemperatur",
    "z2roomzonemapping": "Zone 2 Raumtemperatursensor-Zuordnung",
    "z2sfmode": "Zone 2 Sonderbetrieb",
    "z2shortname": "Zone 2 Kurzname",
    "z2timer_friday0_htm": "Zone 2 Zeitfenster Freitag Beginn",
    "z2timer_friday0_htm_1": "Zone 2 Zeitfenster Freitag Ende",
    "z2timer_friday0_slottemp": "Zone 2 Zeitfenster Freitag Temperatur",
    "z2timer_monday0_htm": "Zone 2 Zeitfenster Montag Beginn",
    "z2timer_monday0_htm_1": "Zone 2 Zeitfenster Montag Ende",
    "z2timer_monday0_slottemp": "Zone 2 Zeitfenster Montag Temperatur",
    "z2timer_saturday0_htm": "Zone 2 Zeitfenster Samstag Beginn",
    "z2timer_saturday0_htm_1": "Zone 2 Zeitfenster Samstag Ende",
    "z2timer_saturday0_slottemp": "Zone 2 Zeitfenster Samstag Temperatur",
    "z2timer_sunday0_htm": "Zone 2 Zeitfenster Sonntag Beginn",
    "z2timer_sunday0_htm_1": "Zone 2 Zeitfenster Sonntag Ende",
    "z2timer_sunday0_slottemp": "Zone 2 Zeitfenster Sonntag Temperatur",
    "z2timer_thursday0_htm": "Zone 2 Zeitfenster Donnerstag Beginn",
    "z2timer_thursday0_htm_1": "Zone 2 Zeitfenster Donnerstag Ende",
    "z2timer_thursday0_slottemp": "Zone 2 Zeitfenster Donnerstag Temperatur",
    "z2timer_timeframes_slotcount": "Zone 2 Zeitfenster Anzahl Montag",
    "z2timer_timeframes_slotcount_1": "Zone 2 Zeitfenster Anzahl Dienstag",
    "z2timer_timeframes_slotcount_2": "Zone 2 Zeitfenster Anzahl Mittwoch",
    "z2timer_timeframes_slotcount_3": "Zone 2 Zeitfenster Anzahl Donnerstag",
    "z2timer_timeframes_slotcount_4": "Zone 2 Zeitfenster Anzahl Freitag",
    "z2timer_timeframes_slotcount_5": "Zone 2 Zeitfenster Anzahl Samstag",
    "z2timer_timeframes_slotcount_6": "Zone 2 Zeitfenster Anzahl Sonntag",
    "z2timer_tuesday0_htm": "Zone 2 Zeitfenster Dienstag Beginn",
    "z2timer_tuesday0_htm_1": "Zone 2 Zeitfenster Dienstag Ende",
    "z2timer_tuesday0_slottemp": "Zone 2 Zeitfenster Dienstag Temperatur",
    "z2timer_wednesday0_htm": "Zone 2 Zeitfenster Mittwoch Beginn",
    "z2timer_wednesday0_htm_1": "Zone 2 Zeitfenster Mittwoch Ende",
    "z2timer_wednesday0_slottemp": "Zone 2 Zeitfenster Mittwoch Temperatur",
    "z2valvestatus": "Zone 2 Ventilstatus",
    "z3actualroomtempdesired": "Zone 3 Raum-Sollwert aktuell",
    "z3daytemp": "Zone 3 Tag-Sollwert",
    "z3holidayendperiod": "Zone 3 Urlaub Ende",
    "z3holidaystartperiod": "Zone 3 Urlaub Beginn",
    "z3holidaytemp": "Zone 3 Urlaubs-Sollwert",
    "z3name1": "Zone 3 Name Teil 1",
    "z3name2": "Zone 3 Name Teil 2",
    "z3nighttemp": "Zone 3 Absenk-Sollwert",
    "z3opmode": "Zone 3 Betriebsart",
    "z3quickvetoduration": "Zone 3 Quick-Veto Dauer",
    "z3quickvetoenddate": "Zone 3 Quick-Veto Ende Datum",
    "z3quickvetoendtime": "Zone 3 Quick-Veto Ende Uhrzeit",
    "z3quickvetotemp": "Zone 3 Quick-Veto Sollwert",
    "z3roomzonemapping": "Zone 3 Raumtemperatursensor-Zuordnung",
    "z3sfmode": "Zone 3 Sonderbetrieb",
    "z3shortname": "Zone 3 Kurzname",
    "z3valvestatus": "Zone 3 Ventilstatus",
    # VR 71
    "sensordata1_ignore": "Sensordaten 1 Rest",
    "sensordata1_s1": "Temperatursensor S1",
    "sensordata1_s2": "Temperatursensor S2",
    "sensordata1_s3": "Temperatursensor S3",
    "sensordata1_s4": "Temperatursensor S4",
    "sensordata1_s5": "Temperatursensor S5",
    "sensordata1_s6": "Temperatursensor S6",
    "sensordata1_s7": "Temperatursensor S7",
    "sensordata2_ignore": "Sensordaten 2 Rest",
    "sensordata2_s10": "Temperatursensor S10",
    "sensordata2_s11": "Temperatursensor S11",
    "sensordata2_s12": "Temperatursensor S12",
    "sensordata2_s8": "Temperatursensor S8",
    "sensordata2_s9": "Temperatursensor S9",
    "sensordata2_sx": "Temperatursensor SX",
    # VR 940f, eloBlock
    "currenterror_error": "Aktueller Fehler 1",
    "currenterror_error_1": "Aktueller Fehler 2",
    "currenterror_error_2": "Aktueller Fehler 3",
    "currenterror_error_3": "Aktueller Fehler 4",
    "currenterror_error_4": "Aktueller Fehler 5",
    # other devices
    "electricpower": "Stromaufnahme Wärmepumpe",
    "modulation": "Modulation",
    "returntemp": "Rücklauftemperatur",
    "vdatetime": "Datum/Uhrzeit",
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
    {"UCH", "SCH", "UIN", "SIN", "ULG", "U2L", "D1B", "D1C", "D2B", "D2C", "FLT", "EXP", "EXR"})


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
            # reads and updates (broadcasts) are sensors -- a 'uw' message is both
            # a sensor and a writable, like the library classifies it
            if m.direction[:1] in ("r", "u"):
                put(sensor, key, name, states)
    return {"sensor": sensor, "number": number, "select": select}


def build_strings(msgs: list[MessageDef], lang: str) -> dict:
    """A full HA strings/translations file for one language (UI + entities)."""
    return {**CONFIG.get(lang, {}), "entity": build_entities(msgs, lang)}
