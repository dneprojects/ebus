"""Runtime generation of the integration's translation files, on the user's own
machine, from the upstream per-language CSVs.

At setup the aioebus catalog loads the display text of the scanned devices from
the ebus.github.io CDN -- in the user's language and in English, cached like the
definitions -- and this module writes ``translations/<lang>.json`` locally. The GPL-3.0
upstream text therefore lives only on the user's machine, never in this
repository; Home Assistant then localizes entity names per user through its own
translation system. The files are shared by all entries: each entry's part is
cached by a signature of its devices, the languages and the integration version,
so the CDN is only fetched when one of those changes -- and an entry's run keeps
the names of entries not loaded (yet), so two adapters never undo each other.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from aioebus.catalog import Catalog
from aioebus.messages import MessageDef
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from homeassistant.util.file import WriteError, write_utf8_file_atomic

from . import strings_gen
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
_SCHEMA = 4  # bump to force regeneration when the generated structure changes
_PKG = Path(__file__).parent
_TDIR = _PKG / "translations"
_MARKER = _TDIR / ".ebus_gen"
_FETCH_BUDGET = 20  # s for the whole CDN fetch at setup


def _files_digest(languages: list[str]) -> str:
    h = hashlib.sha1()
    for lang in languages:
        h.update((_TDIR / f"{lang}.json").read_bytes())
    return h.hexdigest()


def _read_marker() -> tuple[dict[str, str], str]:
    """The entries' signatures the files were generated for, and the digest of
    the files then; ({}, "") when there is none (or an older form)."""
    try:
        sigs, digest = _MARKER.read_text(encoding="utf-8").splitlines()[:2]
        stored = json.loads(sigs)
    except (OSError, ValueError):
        return {}, ""
    if not isinstance(stored, dict):
        return {}, ""
    return {str(k): str(v) for k, v in stored.items()}, digest


def _covered(sigs: dict[str, str], languages: list[str]) -> bool:
    """Whether the files were generated for every given entry as it is now. The
    digest of the files is part of it, so files replaced behind our back (an
    update shipping the plain baseline) regenerate."""
    stored, digest = _read_marker()
    try:
        current = _files_digest(languages)
    except OSError:
        return False
    return digest == current and all(
        sig and stored.get(entry) == sig for entry, sig in sigs.items())


def _merged(files: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The new files with the entity names of the current ones they lack kept
    (another adapter's devices, not loaded in this run)."""
    out: dict[str, dict[str, Any]] = {}
    for lang, data in files.items():
        try:
            current = json.loads((_TDIR / f"{lang}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
        entity = {platform: dict(names) for platform, names in current.get("entity", {}).items()
                  if isinstance(names, dict)}
        for platform, names in data.get("entity", {}).items():
            entity.setdefault(platform, {}).update(names)
        out[lang] = {**data, "entity": entity}
    return out


def _write_files(files: dict[str, dict[str, Any]], sigs: dict[str, str]) -> bool:
    # atomic: Home Assistant may read a translation file at any moment
    try:
        _TDIR.mkdir(exist_ok=True)
        for lang, data in files.items():
            write_utf8_file_atomic(
                str(_TDIR / f"{lang}.json"), json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        write_utf8_file_atomic(
            str(_MARKER), f"{json.dumps(sigs, sort_keys=True)}\n{_files_digest(sorted(files))}\n")
    except (OSError, WriteError) as err:  # e.g. a read-only install: names stay as shipped
        _LOGGER.debug("eBUS: translation files not written: %s", err)
        return False
    return True


def _write_generated(files: dict[str, dict[str, Any]], sigs: dict[str, str],
                     keep: set[str]) -> bool:
    """Write the files for the loaded entries (``sigs``), keeping what the files
    hold for the other configured entries (``keep``) and their signatures."""
    stored, _digest = _read_marker()
    others = {entry: sig for entry, sig in stored.items() if entry in keep and entry not in sigs}
    return _write_files(_merged(files), {**others, **sigs})


def _with_missing_names(files: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]] | None:
    """The current translation files plus the entity names they lack (e.g. for a
    device from a user definition, which no shipped file covers); names already
    there, such as CDN text, are kept. None when nothing is missing."""
    merged: dict[str, dict[str, Any]] = {}
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


def _write_base_if_needed(files: dict[str, dict[str, Any]]) -> bool:
    merged = _with_missing_names(files)
    # no signatures: the next start fetches the CDN again
    return merged is not None and _write_files(merged, {})


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

type _Sources = dict[str, Catalog]


def _sources(hass: HomeAssistant) -> _Sources:
    """The scanned devices of every loaded entry (the files are shared by all)."""
    sources: _Sources = hass.data.setdefault(_DATA_KEY, {})
    return sources


def _all_defs(hass: HomeAssistant) -> list[MessageDef]:
    return [m for catalog in _sources(hass).values() for m in catalog.all_messages()]


async def async_update_translations(
    hass: HomeAssistant, entry_id: str, catalog: Catalog
) -> None:
    """Fetch upstream display text for the scanned devices of every adapter and
    write local translation files (they are shared by all entries). Best-effort:
    without CDN text the names come from the definitions themselves."""
    _sources(hass)[entry_id] = catalog
    lock: asyncio.Lock = hass.data.setdefault(f"{_DATA_KEY}_lock", asyncio.Lock())
    async with lock:  # one writer at a time; each run covers every entry
        await _async_update(hass)


def forget_entry(hass: HomeAssistant, entry_id: str) -> None:
    _sources(hass).pop(entry_id, None)


def _signature(version: object, languages: list[str], catalog: Catalog) -> str:
    """Cache key of one entry's part (not security): it changes with the schema,
    the integration version (the name rules in strings_gen change with it), the
    languages, the CDN paths or the resolved definitions."""
    return hashlib.sha1("|".join((
        str(_SCHEMA), str(version), ",".join(languages), ",".join(catalog.text_paths),
        ",".join(sorted(f"{m.name}:{m.pbsb}:{m.msgid}" for m in catalog.all_messages())),
    )).encode()).hexdigest()


async def _async_update(hass: HomeAssistant) -> None:
    languages = sorted({hass.config.language or "en", "en"})
    sources = _sources(hass)
    paths = sorted({p for catalog in sources.values() for p in catalog.text_paths})
    if not paths:
        await _async_write_base(hass, languages)
        return
    version = (await async_get_integration(hass, DOMAIN)).version
    sigs = {entry: _signature(version, languages, catalog) for entry, catalog in sources.items()}
    if await hass.async_add_executor_job(_covered, sigs, languages):
        return  # already generated for these entries' devices + languages

    try:  # never holding up the setup for long
        async with asyncio.timeout(_FETCH_BUDGET):
            texts = {entry: await catalog.load_texts(languages)
                     for entry, catalog in sources.items()}
    except TimeoutError:
        _LOGGER.debug("eBUS: CDN too slow, names stay as they are for now")
        await _async_write_base(hass, languages)
        return
    if not any("en" in t.languages for t in texts.values()):  # the English base is a must
        await _async_write_base(hass, languages)
        return
    defs = [m for t in texts.values() for m in t.messages]
    files = {lang: strings_gen.build_strings(defs, lang) for lang in languages}
    # only a complete fetch is remembered: after one the CDN could not fully
    # serve (unreachable, timed out) the next start tries again instead of
    # keeping the gap; a file it does not have at all is final
    written = {entry: sig if texts[entry].complete else "" for entry, sig in sigs.items()}
    keep = {e.entry_id for e in hass.config_entries.async_entries(DOMAIN)}
    if not await hass.async_add_executor_job(_write_generated, files, written, keep):
        return
    _clear_cache(hass)
    _LOGGER.info("eBUS: generated %s translations from %d CDN file(s)",
                 "/".join(languages), len(paths))


async def _async_write_base(hass: HomeAssistant, languages: list[str]) -> None:
    """Without CDN text, make sure every entity still has a name: write the names
    the definitions themselves give, but only where a current file lacks one."""
    defs = _all_defs(hass)
    files = {lang: strings_gen.build_strings(defs, lang) for lang in languages}
    if await hass.async_add_executor_job(_write_base_if_needed, files):
        _clear_cache(hass)
        _LOGGER.info("eBUS: wrote %s names from the definitions", "/".join(languages))
