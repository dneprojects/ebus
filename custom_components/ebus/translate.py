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
from dataclasses import replace
from pathlib import Path

import aiohttp
from aioebus.convert import attach_translations
from aioebus.messages import parse_csv
from aioebus.scan import ScanResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration
from homeassistant.util.file import WriteError, write_utf8_file_atomic

from . import strings_gen
from .const import DOMAIN
from .definitions import all_messages, cdn_paths

_LOGGER = logging.getLogger(__name__)
_CDN = "https://ebus.github.io"
_SCHEMA = 4  # bump to force regeneration when the generated structure changes
_PKG = Path(__file__).parent
_TDIR = _PKG / "translations"
_MARKER = _TDIR / ".ebus_gen"
_FETCH_BUDGET = 20  # s for the whole CDN fetch at setup


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


def _write_files(files: dict[str, dict], sig: str) -> bool:
    # atomic: Home Assistant may read a translation file at any moment
    try:
        _TDIR.mkdir(exist_ok=True)
        for lang, data in files.items():
            write_utf8_file_atomic(
                str(_TDIR / f"{lang}.json"), json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        write_utf8_file_atomic(str(_MARKER), f"{sig}\n{_files_digest(sorted(files))}\n")
    except (OSError, WriteError) as err:  # e.g. a read-only install: names stay as shipped
        _LOGGER.debug("eBUS: translation files not written: %s", err)
        return False
    return True


def _with_missing_names(files: dict[str, dict]) -> dict[str, dict] | None:
    """The current translation files plus the entity names they lack (e.g. for a
    device from a user definition, which no shipped file covers); names already
    there, such as CDN text, are kept. None when nothing is missing."""
    merged: dict[str, dict] = {}
    changed = False
    for lang, data in files.items():
        try:
            current = json.loads((_TDIR / f"{lang}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            merged[lang], changed = data, True
            continue
        have = current.setdefault("entity", {})
        for platform, keys in data.get("entity", {}).items():
            names = have.setdefault(platform, {})
            for key, value in keys.items():
                if key not in names:
                    names[key], changed = value, True
        merged[lang] = current
    return merged if changed else None


def _write_base_if_needed(files: dict[str, dict]) -> bool:
    merged = _with_missing_names(files)
    # no signature: the next start fetches the CDN again
    return merged is not None and _write_files(merged, "")


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


_DATA_KEY = f"{DOMAIN}_translation_sources"

type _Sources = dict[str, tuple[dict[int, ScanResult], Path | None]]


def _sources(hass: HomeAssistant) -> _Sources:
    """The scanned devices of every loaded entry (the files are shared by all)."""
    sources: _Sources = hass.data.setdefault(_DATA_KEY, {})
    return sources


def _all_defs(hass: HomeAssistant) -> list:
    return [m for devices, user_dir in _sources(hass).values()
            for m in all_messages(devices, user_dir)]


async def async_update_translations(
    hass: HomeAssistant, entry_id: str, devices: dict[int, ScanResult], user_dir: Path | None
) -> None:
    """Fetch upstream display text for the scanned devices of every adapter and
    write local translation files (they are shared by all entries). Best-effort:
    without CDN text the names come from the definitions themselves."""
    _sources(hass)[entry_id] = (devices, user_dir)
    lock: asyncio.Lock = hass.data.setdefault(f"{_DATA_KEY}_lock", asyncio.Lock())
    async with lock:  # one writer at a time; each run covers every entry
        await _async_update(hass)


def forget_entry(hass: HomeAssistant, entry_id: str) -> None:
    _sources(hass).pop(entry_id, None)


async def _async_update(hass: HomeAssistant) -> None:
    languages = sorted({hass.config.language or "en", "en"})
    paths = sorted({p for devices, _ in _sources(hass).values() for p in cdn_paths(devices)})
    if not paths:
        await _async_write_base(hass, languages)
        return
    defs = _all_defs(hass)
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
        await _async_write_base(hass, languages)
        return
    by_lang: dict[str, list] = {}
    for (lang, _path), text in zip(jobs, texts, strict=True):
        if text:
            by_lang.setdefault(lang, []).extend(parse_csv(text))
    if "en" not in by_lang:  # the English base must be present
        await _async_write_base(hass, languages)
        return

    # fresh copies: attaching texts must not touch the cached definitions
    defs = [_copy(m) for m in _all_defs(hass)]
    attach_translations(defs, by_lang)
    files = {lang: strings_gen.build_strings(defs, lang) for lang in languages}
    # only a complete fetch is remembered: after one the CDN could not fully
    # serve (unreachable, timed out) the next start tries again instead of
    # keeping the gap; a file it does not have at all is final
    complete = all(text is not None for text in texts)
    if not await hass.async_add_executor_job(_write_files, files, sig if complete else ""):
        return
    _clear_cache(hass)
    _LOGGER.info("eBUS: generated %s translations from %d CDN file(s)",
                 "/".join(languages), len(paths))


def _copy(msg):
    return replace(msg, fields=[replace(f) for f in msg.fields])


async def _async_write_base(hass: HomeAssistant, languages: list[str]) -> None:
    """Without CDN text, make sure every entity still has a name: write the names
    the definitions themselves give, but only where a current file lacks one."""
    defs = _all_defs(hass)
    files = {lang: strings_gen.build_strings(defs, lang) for lang in languages}
    if await hass.async_add_executor_job(_write_base_if_needed, files):
        _clear_cache(hass)
        _LOGGER.info("eBUS: wrote %s names from the definitions", "/".join(languages))
