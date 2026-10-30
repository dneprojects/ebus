"""Runtime generation of the integration's translation files, on the user's own
machine, from the upstream per-language CSVs.

At setup we fetch the display text for the scanned devices from the ebus.github.io
CDN -- in the user's language and in English -- merge it onto the (facts-only)
bundled definitions, and write ``translations/<lang>.json`` locally. The GPL-3.0
upstream text therefore lives only on the user's machine, never in this
repository; Home Assistant then localizes entity names per user through its own
translation system. It is cached by a signature of the device set and languages,
so the CDN is only fetched when that changes.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path

import aiohttp
from aioebus.convert import attach_translations
from aioebus.messages import parse_csv
from aioebus.scan import ScanResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import strings_gen
from .library import _index, all_messages, resolve_file

_LOGGER = logging.getLogger(__name__)
_CDN = "https://ebus.github.io"
_SCHEMA = 4  # bump to force regeneration when the generated structure changes
_PKG = Path(__file__).parent
_TDIR = _PKG / "translations"
_MARKER = _TDIR / ".ebus_gen"


def _cdn_paths(devices: dict[int, ScanResult]) -> list[str]:
    cdn = _index().get("cdn", {})
    paths: set[str] = set()
    for scan in devices.values():
        file = resolve_file(scan.ident, scan.hw)
        if file:
            paths.update(cdn.get(file, []))
    return sorted(paths)


async def _fetch(session, lang: str, path: str) -> str | None:
    url = f"{_CDN}/{lang}/{path}.csv"
    try:
        async with asyncio.timeout(15):
            resp = await session.get(url)
            if resp.status != 200:
                return None
            return await resp.text()
    except (TimeoutError, aiohttp.ClientError) as err:
        _LOGGER.debug("eBUS: CDN %s unreachable: %s", url, err)
        return None


def _marker_matches(sig: str) -> bool:
    try:
        return _MARKER.read_text(encoding="utf-8").strip() == sig
    except OSError:
        return False


def _write_files(files: dict[str, dict], sig: str) -> None:
    _TDIR.mkdir(exist_ok=True)
    for lang, data in files.items():
        (_TDIR / f"{lang}.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _MARKER.write_text(sig, encoding="utf-8")


def _clear_cache(hass: HomeAssistant) -> None:
    """Drop HA's flattened-translation cache so the freshly written files are
    re-read. The frontend still serves its own cached copy until the page is
    reloaded, so a restart (or browser refresh) is what actually shows the new
    names -- this just makes that refresh pick them up instead of the old cache."""
    try:
        from homeassistant.helpers.translation import TRANSLATION_FLATTEN_CACHE
        hass.data.pop(TRANSLATION_FLATTEN_CACHE, None)
    except Exception as err:  # noqa: BLE001 - best effort; a restart applies it anyway
        _LOGGER.debug("eBUS: could not clear translation cache: %s", err)


async def async_update_translations(
    hass: HomeAssistant, devices: dict[int, ScanResult], user_dir: Path | None
) -> None:
    """Fetch upstream display text for the scanned devices and write local
    translation files. Best-effort: a device with no CDN mapping, or an
    unreachable CDN, simply leaves names in English."""
    paths = _cdn_paths(devices)
    if not paths:
        return
    languages = sorted({hass.config.language or "en", "en"})
    defs = all_messages(devices, user_dir)
    # Cache key (not security): regenerate when the schema, languages, CDN paths,
    # OR the resolved definition set change -- so adding a bundled message (e.g. a
    # new register) re-localizes on its own, without a manual _SCHEMA bump.
    sig = hashlib.sha1("|".join((
        str(_SCHEMA), ",".join(languages), ",".join(paths),
        ",".join(sorted(f"{m.name}:{m.pbsb}:{m.msgid}" for m in defs)),
    )).encode()).hexdigest()
    if await hass.async_add_executor_job(_marker_matches, sig):
        return  # already generated for this device set + languages + definitions

    session = async_get_clientsession(hass)
    by_lang: dict[str, list] = {}
    for lang in languages:
        msgs: list = []
        for path in paths:
            text = await _fetch(session, lang, path)
            if text:
                msgs += parse_csv(text)
        if msgs:
            by_lang[lang] = msgs
    if "en" not in by_lang:  # the English base must be present
        return

    defs = all_messages(devices, user_dir)
    attach_translations(defs, by_lang)
    files = {lang: strings_gen.build_strings(defs, lang) for lang in languages}
    await hass.async_add_executor_job(_write_files, files, sig)
    _clear_cache(hass)
    _LOGGER.info("eBUS: generated %s translations from %d CDN file(s)",
                 "/".join(languages), len(paths))
