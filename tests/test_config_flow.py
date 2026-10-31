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
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ebus.const import DOMAIN

_OK = AsyncMock(return_value=None)  # _probe_adapter returns None on success


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    # no adapter web API by default (and no real HTTP in tests)
    with patch("custom_components.ebus.config_flow._adapter_id", AsyncMock(return_value=None)):
        yield


async def test_same_adapter_at_a_new_ip_updates_its_entry(hass: HomeAssistant):
    # keyed by the adapter's own id (MAC), so a changed IP is recognised
    entry = MockConfigEntry(domain=DOMAIN, unique_id="58e6c5e3ba58",
                            data={"host": "192.168.178.179", "port": 9999},
                            version=1, minor_version=4)
    entry.add_to_hass(hass)
    with patch("custom_components.ebus.config_flow._adapter_id",
               AsyncMock(return_value="58e6c5e3ba58")), \
         patch("custom_components.ebus.config_flow._probe_adapter", _OK), \
         patch("custom_components.ebus.async_setup_entry", return_value=True):
        init = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF},
            data=ZeroconfServiceInfo(
                ip_address=ip_address("192.168.178.50"),
                ip_addresses=[ip_address("192.168.178.50")], port=9999,
                hostname="ebus-dne.local.", type="_ebusd._tcp.local.",
                name="ebus-dne._ebusd._tcp.local.", properties={}))
    assert init["type"] is FlowResultType.ABORT
    assert init["reason"] == "already_configured"
    assert entry.data["host"] == "192.168.178.50"


async def test_new_ip_does_not_schedule_a_second_reload(hass: HomeAssistant):
    # the entry's own update listener reloads it; the flow must not add another
    entry = MockConfigEntry(domain=DOMAIN, unique_id="58e6c5e3ba58",
                            data={"host": "192.168.178.179", "port": 9999},
                            version=1, minor_version=4, state=config_entries.ConfigEntryState.LOADED)
    entry.add_to_hass(hass)
    with patch("custom_components.ebus.config_flow._adapter_id",
               AsyncMock(return_value="58e6c5e3ba58")), \
         patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF},
            data=ZeroconfServiceInfo(
                ip_address=ip_address("192.168.178.50"),
                ip_addresses=[ip_address("192.168.178.50")], port=9999,
                hostname="ebus-dne.local.", type="_ebusd._tcp.local.",
                name="ebus-dne._ebusd._tcp.local.", properties={}))
    reload.assert_not_called()
    assert entry.data["host"] == "192.168.178.50"


async def test_wrong_port_never_rewrites_a_working_entry(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="58e6c5e3ba58",
                            data={"host": "192.168.178.179", "port": 9999},
                            version=1, minor_version=4)
    entry.add_to_hass(hass)
    with patch("custom_components.ebus.config_flow._adapter_id",
               AsyncMock(return_value="58e6c5e3ba58")), \
         patch("custom_components.ebus.config_flow._probe_adapter",
               AsyncMock(return_value="no_ebus_data")):
        init = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            init["flow_id"], {"host": "192.168.178.179", "port": 3333, "allow_write": False})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_ebus_data"}
    assert entry.data["port"] == 9999


async def test_known_adapter_is_not_offered_again_when_its_web_api_is_busy(
        hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="58e6c5e3ba58",
                            data={"host": "192.168.178.179", "port": 9999},
                            version=1, minor_version=4)
    entry.add_to_hass(hass)
    probe = AsyncMock(return_value=None)
    with patch("custom_components.ebus.config_flow._probe_adapter", probe):  # id: None
        init = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_discovery())
    assert init["type"] is FlowResultType.ABORT
    assert init["reason"] == "already_configured"
    probe.assert_not_called()


async def test_user_flow_creates_entry(hass: HomeAssistant):
    with patch("custom_components.ebus.config_flow._probe_adapter", _OK), \
         patch("custom_components.ebus.async_setup_entry", return_value=True):
        init = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            init["flow_id"], {"host": "1.2.3.4", "port": 9999, "allow_write": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"host": "1.2.3.4", "port": 9999}
    assert result["options"] == {"allow_write": True}


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
        result = await hass.config_entries.flow.async_configure(
            init["flow_id"], {"allow_write": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"host": "192.168.178.179", "port": 9999}
    assert result["options"] == {"allow_write": False}


async def test_zeroconf_aborts_when_not_an_adapter(hass: HomeAssistant):
    with patch("custom_components.ebus.config_flow._probe_adapter",
               AsyncMock(return_value="no_ebus_data")):
        init = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_discovery())
    assert init["type"] is FlowResultType.ABORT
    assert init["reason"] == "no_ebus_data"
