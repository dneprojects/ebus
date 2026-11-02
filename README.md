# eBUS for Home Assistant

A **local** Home Assistant integration for Vaillant/eBUS heating systems. It talks
to an enhanced eBUS adapter directly through the [aioebus](https://github.com/dneprojects/aioebus)
library — **no ebusd daemon and no MQTT**. The integration is deliberately thin:
all protocol and device knowledge (framing, CRC, datatypes, discovery, device
definitions, conditions, reads/writes) lives in aioebus; the integration decides
how it appears in Home Assistant.

## Status

Early. The integration connects to the adapter, scans the bus for devices
(0704 identification) and runs the aioebus **passive-first engine**: it decodes
the telegrams on the bus for free and actively polls only the registers that are
due, publishing them as sensors grouped per device. Writable entities
(number/select) and auto-generated entities are next.

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
remove the repository in HACS). Definitions downloaded for devices outside the
bundled set are cached in `.storage/ebus_defs` of your configuration; delete that
folder as well.

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
   and selects), localised through Home Assistant translations.
