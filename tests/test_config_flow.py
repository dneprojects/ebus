"""Config-flow tests (run under pytest-homeassistant-custom-component in CI;
skipped where Home Assistant is not installed)."""
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ebus.const import DOMAIN


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


async def test_user_flow_creates_entry(hass: HomeAssistant):
    with patch("custom_components.ebus.config_flow._can_connect",
               AsyncMock(return_value=True)), \
         patch("custom_components.ebus.async_setup_entry", return_value=True):
        init = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            init["flow_id"],
            {"host": "1.2.3.4", "port": 9999, "download_defs": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"host": "1.2.3.4", "port": 9999}


async def test_user_flow_cannot_connect(hass: HomeAssistant):
    with patch("custom_components.ebus.config_flow._can_connect",
               AsyncMock(return_value=False)):
        init = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            init["flow_id"],
            {"host": "nope", "port": 9999, "download_defs": False})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
