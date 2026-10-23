# eBUS for Home Assistant

A **local** Home Assistant integration for Vaillant/eBUS heating systems. It talks
to an enhanced eBUS adapter directly through the [aioebus](https://github.com/dneprojects/aioebus)
library — **no ebusd daemon and no MQTT**. The integration is deliberately thin:
all protocol work (framing, CRC, datatypes, scan, conditions, reads/writes) lives
in aioebus.

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
remove the repository in HACS). If you enabled the optional definition download,
delete the generated `ebus_defs` folder in your config directory as well.

## How it works

1. On setup it scans known eBUS addresses and creates a device per responder
   (manufacturer, id, software/hardware version from the 0704 answer).
2. Each device is matched to a definition **by its scan ident (+ HW variant)**,
   not by address (`configs/vaillant/index.json`). The bus address is stamped on
   at runtime, so two identical devices behind different bus couplers share one
   definition file -- adding a second heat pump needs no second file.
3. The engine decodes passive bus traffic for free and actively polls the rest,
   publishing decoded values as sensors (and, when writing is enabled, numbers
   and selects), localised through Home Assistant translations.
