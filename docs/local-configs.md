# Covering more devices locally

The integration ships only **our own** Apache-2.0 device definitions. Broad
coverage for every Vaillant device lives in
[ebusd-configuration](https://github.com/john30/ebusd-configuration), which is
**GPL-3.0** — we cannot redistribute a derivative of it under Apache-2.0 (see
`licensing.md`). You can, however, generate definitions **on your own machine**
from **your own copy** of that data: the GPL-3.0 derivative then stays local and
is never distributed by this project.

## Steps

1. Get the eBUS CSV definitions (e.g. clone ebusd-configuration, or use the ones
   your ebusd already has under `.../ebusd-configuration/en/vaillant`).
2. Convert them to the native JSON format with aioebus:

   ```bash
   python -m aioebus.convert /path/to/ebusd-configuration/en/vaillant /config/ebus_defs
   ```

   This writes one `*.json` per device file (same base name) into
   `/config/ebus_defs`.
3. In Home Assistant: **Settings → Devices & Services → eBUS → Configure**, set
   **"Extra device-definition directory"** to that folder (e.g.
   `/config/ebus_defs`), and reload.

The integration matches each scanned device to a file by its scan ident (and HW
variant) and stamps the bus address at runtime — so identical devices behind
different couplers share one file. Our own bundled definitions always take
precedence over the local ones.

> Only *you* hold the generated files; keep them out of any Apache-2.0
> repository. If you improve a definition, consider contributing it back to
> ebusd-configuration.
