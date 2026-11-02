"""Runtime translation cache (run under pytest-homeassistant-custom-component;
skipped where Home Assistant is not installed)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from aioebus.catalog import Catalog
from aioebus.scan import ScanResult
from homeassistant.core import HomeAssistant

from custom_components.ebus import translate

_CSV = ("type,circuit,level,name,comment,qq,zz,pbsb,id,*name,part,type,divisor/values,unit,comment\n"
        "r,,,FlowTemp,flow temperature,,,b509,0d1800,value,,D2C,,°C,\n")
_HMU = {0x08: ScanResult(0xB5, "Vaillant", "HMU00", "0902", "5103", 902, 5103)}


def _url_fetch(fetch):
    """A catalog fetch from a (session, lang, path) stand-in of the CDN."""
    if fetch is None:
        return None

    async def by_url(url):
        lang, path = url.removeprefix("https://ebus.github.io/").split("/", 1)
        return await fetch(None, lang, path.removesuffix(".csv"))

    return by_url


async def _catalog(devices, user_dir=None, fetch=None) -> Catalog:
    catalog = Catalog(user_dir=user_dir, fetch=_url_fetch(fetch))  # texts via fetch
    await catalog.load(devices)
    return catalog


@pytest.fixture
def tdir(tmp_path, monkeypatch):
    monkeypatch.setattr(translate, "_TDIR", tmp_path)
    monkeypatch.setattr(translate, "_MARKER", tmp_path / ".ebus_gen")
    return tmp_path


def test_marker_detects_replaced_files(tdir):
    translate._write_files({"de": {"a": 1}, "en": {"a": 2}}, "sig1")
    assert translate._marker_matches("sig1", ["de", "en"])
    assert not translate._marker_matches("sig2", ["de", "en"])
    # an update put the plain baseline back but left our marker behind
    (tdir / "en.json").write_text('{"a": 3}\n', encoding="utf-8")
    assert not translate._marker_matches("sig1", ["de", "en"])


async def test_new_version_regenerates(hass: HomeAssistant, tdir):
    hass.config.language = "de"
    fetch = AsyncMock(return_value=_CSV)

    async def run(version: str) -> None:
        integration = MagicMock(version=version)
        with patch.object(translate, "async_get_integration",
                          AsyncMock(return_value=integration)):
            await translate.async_update_translations(
                hass, "e1", await _catalog(_HMU, fetch=fetch))

    await run("0.3.1")
    first = fetch.await_count
    assert first > 0 and (tdir / "de.json").exists()
    await run("0.3.1")
    assert fetch.await_count == first  # cached: same version, same files
    await run("0.3.2")
    assert fetch.await_count > first  # name rules may have changed: regenerate


_CTL = {0x15: ScanResult(0xB5, "Vaillant", "CTLV3", "0808", "8004", 808, 8004)}


def _keys(path) -> set[str]:
    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k for keys in data["entity"].values() for k in keys}


async def test_two_adapters_share_the_files(hass: HomeAssistant, tdir):
    # each adapter's setup writes the files: the last one must keep the other's names
    async def fetch(_session, lang, path):
        return _CSV

    with patch.object(translate, "async_get_integration",
                      AsyncMock(return_value=MagicMock(version="0.6.4"))):
        await translate.async_update_translations(hass, "a", await _catalog(_HMU, fetch=fetch))
        hmu_keys = _keys(tdir / "en.json")
        await translate.async_update_translations(hass, "b", await _catalog(_CTL, fetch=fetch))
    keys = _keys(tdir / "en.json")
    assert hmu_keys <= keys and len(keys) > len(hmu_keys)
    translate.forget_entry(hass, "b")
    assert set(translate._sources(hass)) == {"a"}


@pytest.mark.parametrize("cdn", [_CSV, None])
async def test_user_definitions_get_names(hass: HomeAssistant, tdir, tmp_path, cdn):
    # a device only a user definition covers has no CDN text of its own: its names
    # come from the definition -- with the CDN reachable (for the other devices)
    # or not -- so its entities never get nameless ids
    import json
    (tdir / "en.json").write_text(json.dumps({"entity": {"sensor": {}}}), encoding="utf-8")
    user = tmp_path / "user"
    user.mkdir()
    (user / "26.vr70.json").write_text(json.dumps([{
        "direction": "r", "name": "MixerTemp", "pbsb": "b509", "id": "0d0100",
        "fields": [{"name": "value", "type": "D2C", "unit": "°C"}]}]), encoding="utf-8")
    vr70 = {0x26: ScanResult(0xB5, "Vaillant", "VR70", "0100", "0200", 100, 200)}
    catalog = await _catalog(vr70, user, fetch=AsyncMock(return_value=cdn))
    with patch.object(translate, "async_get_integration",
                      AsyncMock(return_value=MagicMock(version="0.8.0"))):
        await translate.async_update_translations(hass, "e1", catalog)
    assert any("mixertemp" in k for k in _keys(tdir / "en.json"))


def test_names_from_definitions_only_fill_gaps(tdir):
    # a CDN-generated file stays as it is; only the missing name is added
    import json
    current = {"title": "eBUS", "entity": {"sensor": {"flowtemp": {"name": "Vorlauf (CDN)"}}}}
    (tdir / "de.json").write_text(json.dumps(current), encoding="utf-8")
    base = {"entity": {"sensor": {"flowtemp": {"name": "FlowTemp"},
                                  "mixertemp": {"name": "MixerTemp"}}}}
    assert translate._write_base_if_needed({"de": base})
    data = json.loads((tdir / "de.json").read_text(encoding="utf-8"))
    assert data["title"] == "eBUS"
    assert data["entity"]["sensor"] == {"flowtemp": {"name": "Vorlauf (CDN)"},
                                        "mixertemp": {"name": "MixerTemp"}}
    assert not translate._write_base_if_needed({"de": base})  # nothing missing now


def test_unwritable_translation_dir_is_quiet(tdir, monkeypatch):
    from homeassistant.util.file import WriteError

    def fail(*_a, **_k):
        raise WriteError("read-only")

    monkeypatch.setattr(translate, "write_utf8_file_atomic", fail)
    assert translate._write_files({"en": {}}, "sig") is False
