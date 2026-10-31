"""Runtime generation of the integration's translation files, on the user's own
machine, from the upstream per-language CSVs.

At setup we fetch the display text for the scanned devices from the ebus.github.io
CDN -- in the user's language and in English -- merge it onto the (facts-only)
bundled definitions, and write ``translations/<lang>.json`` locally. The GPL-3.0
upstream text therefore lives only on the user's machine, never in this
repository; Home Assistant then localizes entity names per user through its own
translation system. It is cached by a signature of the device set, languages and
integration version, so the CDN is only fetched when one of those changes.
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
from homeassistant.loader import async_get_integration

from . import strings_gen
from .const import DOMAIN
from .library import _index, all_messages, resolve_file

_LOGGER = logging.getLogger(__name__)
_CDN = "https://ebus.github.io"
_SCHEMA = 4  # bump to force regeneration when the generated structure changes
_PKG = Path(__file__).parent
_TDIR = _PKG / "translations"
_MARKER = _TDIR / ".ebus_gen"
_FETCH_BUDGET = 20  # s for the whole CDN fetch at setup


def _cdn_paths(devices: dict[int, ScanResult]) -> list[str]:
    cdn = _index().get("cdn", {})
    paths: set[str] = set()
    for scan in devices.values():
        file = resolve_file(scan.ident, scan.hw)
        if file:
            paths.update(cdn.get(file, []))
    return sorted(paths)


async def _fetch(session, lang: str, path: str) -> str | None:
    """The file's text; "" when the CDN has no such file (final: e.g. a language
    upstream does not translate); None when it could not be reached (retry)."""
    url = f"{_CDN}/{lang}/{path}.csv"
    try:
        async with asyncio.timeout(15), session.get(url) as resp:
            if resp.status != 200:
                return ""
            return await resp.text()
    except (TimeoutError, aiohttp.ClientError) as err:
        _LOGGER.debug("eBUS: CDN %s unreachable: %s", url, err)
        return None


def _files_digest(languages: list[str]) -> str:
    h = hashlib.sha1()
    for lang in languages:
        h.update((_TDIR / f"{lang}.json").read_bytes())
    return h.hexdigest()


def _marker_matches(sig: str, languages: list[str]) -> bool:
    """The marker holds the signature AND a digest of the files it wrote, so files
    replaced behind our back (an update shipping the plain baseline) regenerate."""
    if not sig:
        return False
    try:
        return _MARKER.read_text(encoding="utf-8").split() == [sig, _files_digest(languages)]
    except OSError:
        return False


def _write_files(files: dict[str, dict], sig: str) -> None:
    _TDIR.mkdir(exist_ok=True)
    for lang, data in files.items():
        (_TDIR / f"{lang}.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _MARKER.write_text(f"{sig}\n{_files_digest(sorted(files))}\n", encoding="utf-8")


def _clear_cache(hass: HomeAssistant) -> None:
    """Forget this integration's entries in HA's flattened-translation cache so
    the freshly written files are re-read (other integrations' entries stay).
    The frontend still serves its own cached copy until the page is reloaded,
    so a restart (or browser refresh) is what actually shows the new names."""
    try:
        from homeassistant.helpers.translation import TRANSLATION_FLATTEN_CACHE
        cache = hass.data.get(TRANSLATION_FLATTEN_CACHE)
        if cache is None:
            return
        data = cache.cache_data
        for components in data.loaded.values():
            components.discard(DOMAIN)
        for categories in data.cache.values():
            for by_component in categories.values():
                by_component.pop(DOMAIN, None)
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
    version = (await async_get_integration(hass, DOMAIN)).version
    # Cache key (not security): regenerate when the schema, the integration
    # version (the name rules in strings_gen change with it), languages, CDN
    # paths or the resolved definition set change.
    sig = hashlib.sha1("|".join((
        str(_SCHEMA), str(version), ",".join(languages), ",".join(paths),
        ",".join(sorted(f"{m.name}:{m.pbsb}:{m.msgid}" for m in defs)),
    )).encode()).hexdigest()
    if await hass.async_add_executor_job(_marker_matches, sig, languages):
        return  # already generated for this device set + languages + definitions

    session = async_get_clientsession(hass)
    jobs = [(lang, path) for lang in languages for path in paths]
    try:  # all files at once, and never holding up the setup for long
        async with asyncio.timeout(_FETCH_BUDGET):
            texts = await asyncio.gather(*(_fetch(session, lang, path) for lang, path in jobs))
    except TimeoutError:
        _LOGGER.debug("eBUS: CDN too slow, names stay as they are for now")
        return
    by_lang: dict[str, list] = {}
    for (lang, _path), text in zip(jobs, texts, strict=True):
        if text:
            by_lang.setdefault(lang, []).extend(parse_csv(text))
    if "en" not in by_lang:  # the English base must be present
        return

    defs = all_messages(devices, user_dir)
    attach_translations(defs, by_lang)
    files = {lang: strings_gen.build_strings(defs, lang) for lang in languages}
    # only a complete fetch is remembered: after one the CDN could not fully
    # serve (unreachable, timed out) the next start tries again instead of
    # keeping the gap; a file it does not have at all is final
    complete = all(text is not None for text in texts)
    await hass.async_add_executor_job(_write_files, files, sig if complete else "")
    _clear_cache(hass)
    _LOGGER.info("eBUS: generated %s translations from %d CDN file(s)",
                 "/".join(languages), len(paths))
