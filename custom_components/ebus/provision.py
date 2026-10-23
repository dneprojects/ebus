"""Optional, user-initiated download of device definitions.

On request, fetch the eBUS CSV definitions from ebusd-configuration (the user
obtaining them from the original source) and convert them to our JSON format
into a local directory on the user's own machine. This project never
distributes those files; it only offers to generate them locally.
"""
from __future__ import annotations

import io
import logging
import tarfile
from pathlib import Path
from typing import Final

from aioebus.convert import csv_to_json
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

# source repository (ebusd-configuration) tarball and the subtree we convert
TARBALL: Final = "https://github.com/john30/ebusd-configuration/archive/refs/heads/master.tar.gz"
SUBPATH: Final = "/en/vaillant/"
DEFS_DIRNAME: Final = "ebus_defs"


async def download_and_convert(hass: HomeAssistant, dest: Path) -> int:
    """Download the definitions and convert them into `dest`. Returns the count.
    Network is async; the extract + convert runs in the executor."""
    session = async_get_clientsession(hass)
    async with session.get(TARBALL) as resp:
        resp.raise_for_status()
        data = await resp.read()
    return await hass.async_add_executor_job(_extract_convert, data, dest)


def _extract_convert(data: bytes, dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not (member.isfile() and SUBPATH in member.name
                    and member.name.endswith(".csv")):
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            text = extracted.read().decode("utf-8", "replace")
            # write only the basename into dest (no tar path is trusted)
            out = dest / (Path(member.name).stem + ".json")
            try:
                out.write_text(csv_to_json(text) + "\n", encoding="utf-8")
                count += 1
            except Exception as err:  # noqa: BLE001 - skip a single unparseable file
                _LOGGER.debug("skip %s: %s", member.name, err)
    return count
