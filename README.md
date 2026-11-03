# eBUS for Home Assistant

A **local** Home Assistant integration for Vaillant/eBUS heating systems. It talks
to an enhanced eBUS adapter directly through the [aioebus](https://github.com/dneprojects/aioebus)
library — **no ebusd daemon and no MQTT**. The integration is deliberately thin:
all protocol and device knowledge (framing, CRC, datatypes, discovery, device
definitions, conditions, reads/writes) lives in aioebus; the integration decides
how it appears in Home Assistant.

## Status

In use on a Vaillant installation (heat pumps, controller, hot-water and
ventilation units). The integration discovers the devices on the bus and runs the
aioebus **passive-first engine**: it decodes the telegrams on the bus for free and
actively polls only the registers enabled entities need. It provides sensors per
device, numbers and selects for settings (opt-in: writing is off by default), a
thermostat per heating zone, the adapter's own diagnostics, and entity names in
your language.

## Requirements

- An enhanced eBUS adapter reachable over TCP (ebusd's `enh:host:port`). The
  adapter is single-client, so **ebusd must not be using it** at the same time —
  this integration replaces ebusd.
- The [`aioebus`](https://github.com/dneprojects/aioebus) library in Home
  Assistant's environment (listed in the manifest requirements).

## Install

Copy `custom_components/ebus` into your Home Assistant `config/custom_components/`,
or add this repository to HACS as a custom repository. Then add the **eBUS**
integration and enter the adapter host and port (default 9999).

## Removal

Delete the **eBUS** integration from *Settings → Devices & services* (this
removes its entries, devices and entities). To uninstall completely, also remove
the `custom_components/ebus` folder from your Home Assistant configuration (or
remove the repository in HACS). Removing the last eBUS entry also deletes the
definitions it downloaded (`.storage/ebus_defs`) and every entry's stored values.

## How it works

1. On setup aioebus discovers the devices on the bus (scan, Vaillant identification)
   and the integration creates a device per responder.
2. Each device gets its definition from the aioebus catalog: our own bundled
   definitions (by scan ident + HW variant; two identical devices behind different
   bus couplers share one file), else a file from the optional definition directory
   (native JSON or an ebusd CSV), else the matching ebusd-configuration CSV, which
   is downloaded from ebus.github.io on your own machine and cached locally (see
   `docs/licensing.md`).
3. The engine decodes passive bus traffic for free and actively polls the rest,
   publishing decoded values as sensors (and, when writing is enabled, numbers
   and selects), localised through Home Assistant translations. A written value
   is read back, so the entity shows what the device took.
4. When the adapter link drops, aioebus reconnects by itself; after 10 minutes
   without it the bus values count as unavailable.

## Development

```bash
pip install -r requirements_test.txt aioebus   # or a sibling aioebus checkout
ruff check .
mypy custom_components/ebus
python -m pytest -q
python tools/gen_strings.py   # after changing names or definitions
```

Changes are listed in `CHANGELOG.md`. A captured telegram log can be replayed
through the engine with aioebus' `tools/replay.py`.
