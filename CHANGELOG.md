# Changelog

## Unreleased

- Option "Busadresse der Integration": clearer hint (ff lowest priority, 31 recommended)

## 0.7.25

- Our polls give way while an internet gateway (Vaillant VR 9xx) is busy reading the system (5 of its telegrams in 10 s; at most 60 s at a stretch); the count is in the diagnostics
- New option "Busadresse der Integration": our master address on the bus, which decides who goes first when two masters start at once (the lower wins); 31 as ebusd (default), ff gives way to everybody

## 0.7.24

- An internet gateway (Vaillant VR 9xx, ident NETX...) is left alone: none of its registers is polled or written, and once known it is not scanned again at a start; its identification (article, serial, year) is read once and remembered. What it sends is still decoded passively, and its register entities from before are removed

## 0.7.23

- New diagnostic sensor "Gesendete Telegramme pro Minute" on each device that sends on its own (controller, myVAILLANT gateway, VWZ AI, ...), counted passively: e.g. whether the gateway talks to its controller

## 0.7.22

- Logbook: also a read that arrived but got no answer ("keine Antwort") or an empty one although it expects data ("leere Antwort"), with sender, receiver and message -- e.g. the myVAILLANT gateway's reads of the controller; neither counts as an acknowledgment fault nor raises the repair issue

## 0.7.21

- Repair issue "Befehle an … kommen nicht an" only when the same command (sender, receiver, message) is lost 3 times in a row; it clears once that command arrives again (or after 30 minutes without a lost one, when it is no longer sent). A single loss is bus noise the sender repeats: it stays in the logbook and the sensor only

## 0.7.20

- Acknowledgments checked: every command on the bus is checked for its receiver's acknowledgment -- the devices' own (e.g. the controller's relay command to the VR71) passively, without any traffic of ours, and our own reads and writes
- eBUS Adapter: new sensor "Quittungsfehler pro Minute" (commands to devices on the bus, ours included, refused, not acknowledged, taken only on the resend, or with their answer refused)
- Logbook: each such command as "sender → receiver: message, what went wrong" (at most one entry a minute per command and kind, with the count)
- Repair issue "Befehle an … kommen nicht an" when 5 commands to one device are lost within 10 minutes; it clears itself after 30 minutes without a lost one
- Diagnostics: the latest 200 acknowledgment faults

## 0.7.19

- Quick setup: a known installation starts from the devices found last time and scans the bus in the background (Home Assistant no longer waits for it); a new or changed device reloads the entry, a device that misses one scan stays

## 0.7.18

- "Busauslastung" is measured against the bus capacity (it showed far too much); "Fremdabfragen ohne Antwort" counts only reads of devices that are on the bus (a controller probing an empty address is left out)

## 0.7.17

- eBUS Adapter: new sensors "Busauslastung", "Eigene Abfragen pro Minute", "Eigene Abfragen an den Regler pro Minute" and "Fremdabfragen ohne Antwort pro Minute" (over the last minute)
- The system controller is polled gently so its myVAILLANT gateway keeps reaching it: at most one of our reads every 12 s, measurements every 5 min, settings hourly, time programs daily (values the gateway reads arrive passively)

## 0.7.16

- The search for a known device that missed the setup scan runs in the background only; the setup no longer waits for a second try

## 0.7.15

- VR 71 relays: icon by state, closed switch while on
- A device known from an earlier start that misses the scan at setup is tried again and, once it answers, the integration reloads (no more missing VR 71 until the next restart)
- Thermostat and hot water use Home Assistant's native temperature properties (no deprecation warnings; older Home Assistant still served)

## 0.7.14

- Icons: a switch for the VR 71 relays, a valve for the mixer movements (sensoCOMFORT and VR 71)
- Heat pump status code: German names for "defrost time exceeded" and "defrost flow too cold"

## 0.7.13

- VR 71: "Mischerkreis N Mischer" is now "Mischerkreis N Mischerbewegung" without a unit: it is the movement the controller asks for (below 0 closing), not a position

## 0.7.12

- VR 71: new sensors per mixer circuit "Status", "Pumpe", "Mischer" and the flow setpoint sent to the VR 71 (off by default, repeats the controller's)
- A value another device sends (passive) gets its sensor once it arrives: relays the controller marks unused no longer appear, on any installation

## 0.7.11

- VR 71: new sensors "Relais R1"–"Relais R12" show the relay states the controller sends (e.g. the diverter valve); unused relays start disabled

## 0.7.10

- "Current error" shows "Kein Fehler" when a device has none, instead of unknown
- While airing the thermostat offers no temperature setting and shows no setpoint (the controller's is in "Raum-Sollwert aktuell")

## 0.7.9

- New action "Read register": reads any register of a bus device and returns its raw answer; only reads
- New hot water entity: Zeitgesteuert, Manuell, Aus and a one-time charge, setpoint and cylinder temperature
- New sensor per heat pump "COP now": heat output over electric power, none while it delivers no heat
- New heat pump sensors: heat output, condenser outlet temperature, defrost hours and starts, current error
- New controller sensors: system state, system off, bivalence and alternative points, buffer charge offset, current error; VWZ: current error
- Heat pump status codes in German
- Controllers and heat pumps scanning as BASV3, BASS3 or HMUX0 are supported (registers per arotherm-genia-ebusd, not tested here)

## 0.7.8

- While airing the thermostat shows the controller's setpoint (0 °C) but it cannot be changed
- After a mode or preset change the setpoint is read again after 5 s, once the controller has recalculated it
- Preset Stoßlüften shows an open window icon

## 0.7.7

- Sensors and number fields show at most two decimals (heat curve 0.45, not 0.4499999); a number's step is at least 0.01
- Energy (kWh, Wh) shows as a whole number
- Thermostat modes carry the controller's names: Zeitgesteuert, Manuell, Aus (also for zones with their own name)
- Thermostat presets Abwesenheit (from today, open end = one year) and Stoßlüften: set, end and shown as the zone's state tells
- Any single-value register can be written (an away period's dates), not only those with a control

## 0.7.6

- Thermostat as the sensoCOMFORT has it: Auto (time-controlled), Heat (manual), Off; the day/night presets are gone
- The thermostat shows the setpoint in effect now (room setpoint), none while off
- A new temperature in Auto starts a quick veto (setpoint, the controller's duration else 3 h, special function); while heating manually it sets the manual setpoint
- New sensor per zone "Zone N state": off, manual, time slot, setback, Eco, quick veto, away, airing, special function, time-controlled
- The zone mode selects offer off/auto/manual (day/night are gone, automations using them need updating); the circuits' setback mode eco/normal (was night)
- Mode, setpoints, room temperature and special function of a zone are read every minute and right after a change
- Names as the app calls them: "Sollwert manuell", "Absenktemperatur", "Abwesenheitstemperatur", circuit "Absenkmodus" Eco/Normal; a zone without a name is "Zone N"
- Requires aioebus 0.7.6 (pinned)

## 0.7.5

- Writing with read-back and the reconnect loop come from aioebus (`write_confirmed`, `EngineRunner`); the integration keeps only the outage marking and its log
- The value cache is restored through the engine, which drops retired registers itself
- Number ranges are decided in one place (listed, else unit default for floats, else the type's range)
- Bus entities share one base (unavailable after a long outage); enum options from one helper
- Enum states are translated only where the state is a valid key (e.g. not `Mo_Fr`); unused state texts removed
- Entity ids of messages with `_` in their name are read correctly by the migrations
- The platforms declare `PARALLEL_UPDATES`; `quality_scale.yaml` matches the code (discovery, entity categories, stale devices, typing)
- CI runs hassfest and the HACS check; the replay tool moved to aioebus; this changelog
- Requires aioebus 0.7.5 (pinned)

## 0.7.4

- Devices loaded from ebusd CSVs: values other masters read passively decode from the device's answer and are no longer blanked
- A write whose only field lies in the device's answer gets no control; constant fields are no values
- `climate.set_temperature` with `hvac_mode` also switches the zone's mode (only when it differs)
- The extra definition folder can be cleared in the options
- Adding the connected adapter again aborts with "already configured"; "already being set up" has a text
- An identification without a manufacture week shows the year instead of failing

## 0.7.3

- A setup retry no longer wipes the saved values, so entities warm-start once the bus is back
- With two adapters the translation files keep both adapters' names and are not rewritten at every restart
- An adapter whose web API answers late gets its sensors then; a quiet web API no longer overwrites them
- The entry reloads only when the adapter's address or the options change
- A register a definition sends to another module stays on its own device
- Heating zones use the mode roles from aioebus and a translated name unless named on the controller
- Connection check from aioebus; value cache and device housekeeping in own modules; curated German names in names.json
- mypy in CI; tests for retry, two adapters, late web status, climate modes, options, diagnostics, setup failure

## 0.7.2

- What a value is (key, label, numeric, the control a write allows) comes from aioebus.values
- Display texts come through the aioebus catalog (per language, cached); the integration no longer fetches the CDN itself

## 0.7.1

- Definitions come from the aioebus catalog: bundled ones, the definition folder (also ebusd CSVs), else the matching ebusd CSV downloaded from ebus.github.io and cached in `.storage/ebus_defs`
- `presentation.json` keeps the integration's choices: device names, control classes, unconnected channels
- A device without a reviewed definition starts with its read-on-demand registers disabled
- The definition cache goes with the last entry

## 0.7.0

- Device discovery comes from aioebus (scan through the running engine, identification, deep scan, duplicates, late identification)
- Code restructured: definitions, entities, central entity ids, migrations and adapter web status in own modules
- Adapter and identification sensors get translated names
- A heating zone takes its setpoint range from the register's listed bounds
- CI tests against the aioebus release the manifest requires

## 0.6.4

- Setup waits for bus traffic; a silent bus or devices that do not answer retry the setup
- Bus values become unavailable after 10 min without the adapter link; the adapter's own entities keep reporting
- Translation files cover every adapter, are written atomically and only gain names while the CDN is unreachable
- A broken file in the definition folder costs only its device
- Number ranges from aioebus; port range-checked; switching writing off removes the controls
- A stale device can be removed while the entry is not loaded; the value cache is saved with short scan intervals too

## 0.6.3

- Previous-month/-year energy figures carry long-term statistics again as `total` (not an energy-dashboard source)

## 0.6.2

- The heating-circuit minimum cooling flow temperature is a configuration setting, disabled by default

## 0.6.1

- Heating-circuit cut-off and min/max flow temperatures are configuration settings, disabled by default

## 0.6.0

- Only real settings become controls (counters, totals and live states stay sensors), reviewed register by register
- Installer settings are created disabled under configuration
- Temperature, heat-curve and time settings get sensible ranges and steps
- A device whose identification did not answer at startup is asked again during the first hour

## 0.5.0

- German names for every entity, reviewed key by key
- Entity ids no longer collide between two adapters; a new adapter IP keeps the entry (one-time migration)
- Current device registry APIs; definition files read off the event loop
- The identification (article, serial) is remembered
- Faster start: translations fetched in parallel with a time limit; the deep scan runs once
- Stored files of deleted entries are removed; a lost link is logged once per outage
- Requires Home Assistant 2026.8

## 0.4.0

- Writing works end to end: climate setpoints, numbers and selects share one write path that reads the value back
- A write sent but not confirmed is reported as possibly applied, never resent
- Without write permission the thermostat shows no controls

## 0.3.3

- Sensors registered without a name get their label in the entity id (one-time migration)
- Unanswered reads are retried again, so the identification is no longer lost

## 0.3.2

- Localized entity names are regenerated after an update

## 0.3.1

- An adapter that closes the connection no longer freezes Home Assistant; a silent link reconnects after 30 s
- An unreachable adapter at startup makes Home Assistant retry the setup
- New entities are polled without a second restart; climate zones appear once their data arrives
- Retired and renamed entities are cleaned up once by an entry migration (renamed ones keep id and history)
- Past-period kWh values no longer count as running totals

## 0.3.0

- The heat pump behind the V32 coupler uses the same HMU definition as the first one
- One home per value: 16-bit yields and duplicate power registers removed (old registry entries cleaned up)
- Energy counters per heating, hot water and cooling: environment yield, heat output, electricity

## 0.2.1

- Localized names are regenerated when the bundled messages change

## 0.2.0

- First versioned release (HACS installs releases from now on)
- Status01 temperatures named; nine lifetime energy counters (kWh) read passively from the gateway
