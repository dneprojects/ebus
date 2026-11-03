# Licensing

## This integration and aioebus

The eBUS integration (`ebus`) is **Apache-2.0**. It depends on **aioebus**
(Apache-2.0), a pure Python eBUS library.

## Device definitions

aioebus ships a small set of device definitions that are **our own,
independently created** work (Apache-2.0): the eloBlock and the NETX3 gateway
are authored by hand; the HMU heat pump, the sensoCOMFORT controller, the PMW
hot-water unit, the VR 71 and the broadcast messages are re-engineered from a
bus capture of the reference installation, keeping only facts (ids, datatypes,
divisors, units, value tables) of the registers that carried a value there. The
VWZ IO ventilation unit is recognised and named but has no registers on
purpose. Their provenance is documented in aioebus (`defs/vaillant/PROVENANCE.md`).

## Devices outside that set

Broad device coverage lives in
[ebusd-configuration](https://github.com/john30/ebusd-configuration), which is
**GPL-3.0**. We do **not** ship anything derived from it. Instead, for a device
our own definitions do not cover, aioebus downloads the matching CSV from its
public CDN (ebus.github.io) **on the user's own machine** at runtime -- the
same way ebusd itself loads its configuration -- and keeps it in a local cache
(`.storage/ebus_defs` of the Home Assistant configuration). The GPL-3.0 data is
used there, never redistributed by this project. The CDN's display texts are
used the same way to name the entities in the user's language.

A user can also put definitions into the optional definition directory (native
JSON or an ebusd CSV, see `local-configs.md`); our own bundled definitions take
precedence, then those, then the CDN.
