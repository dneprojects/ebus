"""The read-register action (run under pytest-homeassistant-custom-component;
skipped where Home Assistant is not installed)."""
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

import voluptuous as vol
from aioebus.active import ReadError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ebus.const import DOMAIN
from custom_components.ebus.services import SERVICE_READ_REGISTER, async_setup_services


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


def _entry(hass: HomeAssistant, state=ConfigEntryState.LOADED, domain=DOMAIN, engine="default"):
    entry = MockConfigEntry(domain=domain, data={}, state=state)
    entry.add_to_hass(hass)
    if engine == "default":
        engine = MagicMock(read_raw=AsyncMock(return_value=bytes.fromhex("0201c20be31a0000")))
    entry.runtime_data = MagicMock(engine=engine)
    return entry


async def _call(hass: HomeAssistant, entry_id: str, **fields):
    data = {"config_entry_id": entry_id, "address": "08", "command": "b509", **fields}
    return await hass.services.async_call(DOMAIN, SERVICE_READ_REGISTER, data,
                                          blocking=True, return_response=True)


async def test_reads_and_returns_the_answer(hass: HomeAssistant):
    async_setup_services(hass)
    entry = _entry(hass)
    answer = await _call(hass, entry.entry_id, address="18", command="B509", data="540200C20B")
    assert answer == {"answer": "0201c20be31a0000"}
    entry.runtime_data.engine.read_raw.assert_awaited_once_with(0x18, "b509", "540200c20b")


async def test_data_is_optional(hass: HomeAssistant):
    async_setup_services(hass)
    entry = _entry(hass)
    await _call(hass, entry.entry_id)
    entry.runtime_data.engine.read_raw.assert_awaited_once_with(0x08, "b509", "")


@pytest.mark.parametrize("fields", [
    {"address": "8"}, {"address": "xx"}, {"address": "088"},
    {"command": "b5"}, {"command": "b50g"},
    {"data": "abc"}, {"data": "zz"},
])
async def test_malformed_input_is_refused(hass: HomeAssistant, fields):
    async_setup_services(hass)
    entry = _entry(hass)
    with pytest.raises(vol.Invalid):
        await _call(hass, entry.entry_id, **fields)
    entry.runtime_data.engine.read_raw.assert_not_awaited()


async def test_only_a_running_adapter_of_this_integration(hass: HomeAssistant):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "nope")
    with pytest.raises(ServiceValidationError):
        await _call(hass, _entry(hass, state=ConfigEntryState.NOT_LOADED).entry_id)
    with pytest.raises(ServiceValidationError):
        await _call(hass, _entry(hass, domain="other").entry_id)
    with pytest.raises(HomeAssistantError):
        await _call(hass, _entry(hass, engine=None).entry_id)


@pytest.mark.parametrize(("error", "expected"), [
    (ReadError("no answer from slave"), HomeAssistantError),
    (TimeoutError(), HomeAssistantError),
    (ValueError("0x03: not a slave address"), ServiceValidationError),
])
async def test_a_failed_read_is_reported(hass: HomeAssistant, error, expected):
    async_setup_services(hass)
    entry = _entry(hass)
    entry.runtime_data.engine.read_raw.side_effect = error
    with pytest.raises(expected):
        await _call(hass, entry.entry_id)
