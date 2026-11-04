# Changelog

## Unreleased

- Sensors and number fields show at most two decimals (heat curve 0.45, not 0.4499999); a number's step is at least 0.01
- Energy (kWh, Wh) shows as a whole number
- Thermostat modes carry the controller's names: Zeitgesteuert, Manuell, Aus (also for zones with their own name)

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
