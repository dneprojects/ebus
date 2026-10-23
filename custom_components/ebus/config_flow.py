"""Config flow for the eBUS integration."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
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

from .const import (
    CONF_ALLOW_WRITE,
    CONF_CONFIG_DIR,
    CONF_SCAN_INTERVAL,
    DEFAULT_ALLOW_WRITE,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .provision import DEFS_DIRNAME, download_and_convert

_LOGGER = logging.getLogger(__name__)
CONF_DOWNLOAD_DEFS = "download_defs"  # flow-only; triggers a one-off local download


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
    """Ask for the enhanced adapter's host and port."""

    VERSION = 1

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
                options: dict[str, Any] = {}
                if user_input.get(CONF_DOWNLOAD_DEFS):
                    dest = Path(self.hass.config.path(DEFS_DIRNAME))
                    try:
                        count = await download_and_convert(self.hass, dest)
                        options[CONF_CONFIG_DIR] = str(dest)
                        _LOGGER.info("eBUS: downloaded %d device definitions to %s", count, dest)
                    except Exception as err:  # noqa: BLE001 - never fail setup over this
                        _LOGGER.warning("eBUS: definition download failed (%s); "
                                        "you can retry later in the options", err)
                data = {CONF_HOST: host, CONF_PORT: port}
                return self.async_create_entry(title=f"eBUS ({host})", data=data, options=options)
            errors["base"] = probe

        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
            vol.Required(CONF_DOWNLOAD_DEFS, default=True): bool,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

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
        })
        return self.async_show_form(step_id="init", data_schema=schema)
