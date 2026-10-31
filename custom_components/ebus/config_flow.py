"""Config flow for the eBUS integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from aioebus.transport import RECEIVED, EnhDecoder
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import (
    CONF_ALLOW_WRITE,
    CONF_CONFIG_DIR,
    CONF_DEEP_SCAN,
    CONF_SCAN_INTERVAL,
    DEFAULT_ALLOW_WRITE,
    DEFAULT_DEEP_SCAN,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def _probe_adapter(host: str, port: int) -> str | None:
    """Check the adapter is reachable AND actually streaming eBUS data.

    Returns None on success, else an error key. A plain TCP connect is not
    enough: an enhanced adapter streams symbols continuously (SYN while the bus
    is idle), so if we connect but receive no eBUS symbol within a few seconds
    the adapter is not usable as a data source -- typically because another
    client (e.g. ebusd) holds it. That is reported as its own error instead of
    letting setup proceed with no devices."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5)
    except (TimeoutError, OSError):
        return "cannot_connect"
    decoder = EnhDecoder()
    try:
        async with asyncio.timeout(6):
            while True:
                chunk = await reader.read(256)
                if not chunk:
                    return "no_ebus_data"  # adapter closed the stream
                for code, _data in decoder.feed(chunk):
                    if code == RECEIVED:  # a real eBUS symbol -> live adapter
                        return None
    except TimeoutError:
        return "no_ebus_data"  # reachable but silent / held by another client
    finally:
        writer.close()


class EbusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the enhanced adapter's host and port, or confirm a discovered one."""

    VERSION = 1
    # 1.2: registry clean-up of retired/renamed messages; 1.3: suffix nameless ids
    MINOR_VERSION = 3

    def __init__(self) -> None:
        self._discovered: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()
            probe = await _probe_adapter(host, port)
            if probe is None:
                return self.async_create_entry(
                    title=f"eBUS ({host})", data={CONF_HOST: host, CONF_PORT: port},
                    options={CONF_ALLOW_WRITE: user_input[CONF_ALLOW_WRITE]})
            errors["base"] = probe

        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
            vol.Required(CONF_ALLOW_WRITE, default=DEFAULT_ALLOW_WRITE): bool,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle an adapter found via mDNS (it advertises _ebusd._tcp)."""
        host = discovery_info.host
        port = discovery_info.port or DEFAULT_PORT
        await self.async_set_unique_id(f"{host}:{port}")
        self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})
        # only offer it if it is really a usable enh adapter (filters out an ebusd
        # daemon advertising the same service, and an adapter held by another client)
        if await _probe_adapter(host, port) is not None:
            return self.async_abort(reason="no_ebus_data")
        self._discovered = {CONF_HOST: host, CONF_PORT: port}
        self.context["title_placeholders"] = {"host": host}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding the discovered adapter (and set the write opt-in)."""
        host = self._discovered[CONF_HOST]
        if user_input is not None:
            return self.async_create_entry(
                title=f"eBUS ({host})", data=self._discovered,
                options={CONF_ALLOW_WRITE: user_input[CONF_ALLOW_WRITE]})
        schema = vol.Schema({
            vol.Required(CONF_ALLOW_WRITE, default=DEFAULT_ALLOW_WRITE): bool,
        })
        return self.async_show_form(
            step_id="zeroconf_confirm", data_schema=schema,
            description_placeholders={"host": host})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EbusOptionsFlow()


class EbusOptionsFlow(OptionsFlow):
    """Poll interval and the opt-in for writing settings to the bus."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        schema = vol.Schema({
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(int, vol.Range(min=10, max=3600)),
            vol.Required(
                CONF_ALLOW_WRITE,
                default=opts.get(CONF_ALLOW_WRITE, DEFAULT_ALLOW_WRITE),
            ): bool,
            vol.Optional(
                CONF_CONFIG_DIR,
                default=opts.get(CONF_CONFIG_DIR, ""),
            ): str,
            vol.Required(
                CONF_DEEP_SCAN,
                default=opts.get(CONF_DEEP_SCAN, DEFAULT_DEEP_SCAN),
            ): bool,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
