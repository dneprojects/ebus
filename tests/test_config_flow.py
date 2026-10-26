"""Config-flow tests (run under pytest-homeassistant-custom-component in CI;
skipped where Home Assistant is not installed)."""
from ipaddress import ip_address
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from custom_components.ebus.const import DOMAIN

_OK = AsyncMock(return_value=None)  # _probe_adapter returns None on success


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


async def test_user_flow_creates_entry(hass: HomeAssistant):
    with patch("custom_components.ebus.config_flow._probe_adapter", _OK), \
         patch("custom_components.ebus.async_setup_entry", return_value=True):
        init = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            init["flow_id"], {"host": "1.2.3.4", "port": 9999})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"host": "1.2.3.4", "port": 9999}


async def test_user_flow_cannot_connect(hass: HomeAssistant):
    with patch("custom_components.ebus.config_flow._probe_adapter",
               AsyncMock(return_value="cannot_connect")):
        init = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            init["flow_id"], {"host": "nope", "port": 9999})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


def _discovery() -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address("192.168.178.179"),
        ip_addresses=[ip_address("192.168.178.179")],
        port=9999,
        hostname="ebus-dne.local.",
        type="_ebusd._tcp.local.",
        name="ebus-dne._ebusd._tcp.local.",
        properties={},
    )


async def test_zeroconf_discovery_confirm_creates_entry(hass: HomeAssistant):
    with patch("custom_components.ebus.config_flow._probe_adapter", _OK), \
         patch("custom_components.ebus.async_setup_entry", return_value=True):
        init = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_discovery())
        assert init["type"] is FlowResultType.FORM
        assert init["step_id"] == "zeroconf_confirm"
        result = await hass.config_entries.flow.async_configure(init["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"host": "192.168.178.179", "port": 9999}


async def test_zeroconf_aborts_when_not_an_adapter(hass: HomeAssistant):
    with patch("custom_components.ebus.config_flow._probe_adapter",
               AsyncMock(return_value="no_ebus_data")):
        init = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_discovery())
    assert init["type"] is FlowResultType.ABORT
    assert init["reason"] == "no_ebus_data"
