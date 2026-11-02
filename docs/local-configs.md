# Own device definitions

Every device needs a definition. aioebus looks for it in this order:

1. its own bundled definitions (matched by scan ident and HW variant);
2. the optional **definition directory** of this integration;
3. the matching ebusd-configuration CSV, downloaded automatically from
   ebus.github.io and cached in `.storage/ebus_defs` (see `licensing.md`).

So a device the CDN covers needs nothing from you. Use the definition directory
to override the CDN for a device, to work fully offline, or for a device with
your own definition.

## Steps

1. Put the definition files into a folder of your configuration, e.g.
   `/config/ebus_defs_own`. A file is named like ebusd's:
   `ZZ.ident[.variant].csv` (an ebusd CSV as is) or `ZZ.ident[.variant].json`
   (aioebus' native format; convert a CSV with
   `python -m aioebus.convert <csv> <json>`). The ident is matched as a prefix of
   the device's scan ident, a variant `HWnnnn` is preferred for that hardware.
2. In Home Assistant: **Settings → Devices & Services → eBUS → Configure**, set
   **"Extra device-definition directory"** to that folder and reload.

The bus address is stamped on at runtime, so identical devices behind different
couplers share one file.

> A file converted from ebusd-configuration is GPL-3.0 data: keep it on your own
> machine, out of any Apache-2.0 repository. If you improve a definition,
> consider contributing it back to ebusd-configuration.
