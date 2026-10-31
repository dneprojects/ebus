"""Runtime translation cache (run under pytest-homeassistant-custom-component;
skipped where Home Assistant is not installed)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from aioebus.scan import ScanResult
from homeassistant.core import HomeAssistant

from custom_components.ebus import translate

_CSV = ("type,circuit,level,name,comment,qq,zz,pbsb,id,*name,part,type,divisor/values,unit,comment\n"
        "r,,,FlowTemp,flow temperature,,,b509,0d1800,value,,D2C,,°C,\n")
_HMU = {0x08: ScanResult(0xB5, "Vaillant", "HMU00", "0902", "5103", 902, 5103)}


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
        with patch.object(translate, "_fetch", fetch), \
             patch.object(translate, "async_get_integration",
                          AsyncMock(return_value=integration)):
            await translate.async_update_translations(hass, _HMU, None)

    await run("0.3.1")
    first = fetch.await_count
    assert first > 0 and (tdir / "de.json").exists()
    await run("0.3.1")
    assert fetch.await_count == first  # cached: same version, same files
    await run("0.3.2")
    assert fetch.await_count > first  # name rules may have changed: regenerate
