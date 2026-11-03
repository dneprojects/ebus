"""Config flow for the eBUS integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from aioebus.link import probe
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .adapter import fetch_status
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
    """None when the adapter is reachable AND streams eBUS data, else an error
    key: one that is reachable but silent is typically held by another client
    (e.g. ebusd), reported as its own error instead of a setup without devices."""
    try:
        live = await probe(host, port)
    except (TimeoutError, OSError):
        return "cannot_connect"
    return None if live else "no_ebus_data"


async def _adapter_id(hass: HomeAssistant, host: str) -> str | None:
    """The adapter shield's own id (its MAC) from its web API, or None for an
    adapter without one. Keys the entry independently of the IP address."""
    adapter_id = (await fetch_status(hass, host, timeout=3)).get("id")
    return adapter_id if isinstance(adapter_id, str) and adapter_id else None


class EbusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the enhanced adapter's host and port, or confirm a discovered one."""

    VERSION = 1
    # 1.2: registry clean-up of retired/renamed messages; 1.3: suffix nameless ids;
    # 1.4: unique_ids scoped to the entry, adapter keyed independently of its IP;
    # 1.5: number/select entities follow their register's control class;
    # 1.6: heating-circuit limits became configuration; 1.7: so did the cooling limit
    MINOR_VERSION = 7

    def __init__(self) -> None:
        self._discovered: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            await self.async_set_unique_id(
                await _adapter_id(self.hass, host) or f"{host}:{port}")
            self._async_abort_entries_match({CONF_HOST: host, CONF_PORT: port})
            probe = await _probe_adapter(host, port)
            if probe is None:
                # the same adapter under a new, working address updates its entry
                # (only after the probe, so a wrong port never breaks a working
                # entry); its update listener reloads it
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: host, CONF_PORT: port}, reload_on_update=False)
                return self.async_create_entry(
                    title=f"eBUS ({host})", data={CONF_HOST: host, CONF_PORT: port},
                    options={CONF_ALLOW_WRITE: user_input[CONF_ALLOW_WRITE]})
            if any(e.unique_id == self.unique_id and e.state is ConfigEntryState.LOADED
                   for e in self._async_current_entries(include_ignore=False)):
                # the running entry holds the adapter's one connection: that is
                # why the probe saw nothing
                return self.async_abort(reason="already_configured")
            errors["base"] = probe

        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                int, vol.Range(min=1, max=65535)),
            vol.Required(CONF_ALLOW_WRITE, default=DEFAULT_ALLOW_WRITE): bool,
        })
        # newer HA types its schema differently; both accept a voluptuous one
        return self.async_show_form(step_id="user", data_schema=schema,  # type: ignore[arg-type, unused-ignore]
                                    errors=errors)

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle an adapter found via mDNS (it advertises _ebusd._tcp)."""
        host = discovery_info.host
        port = discovery_info.port or DEFAULT_PORT
        # an adapter at a configured address is known, even when its web API is
        # busy for a moment (it must not be probed or offered a second time)
        self._async_abort_entries_match({CONF_HOST: host})
        await self.async_set_unique_id(await _adapter_id(self.hass, host) or f"{host}:{port}")
        # the same adapter at a new IP: follow it (the entry's listener reloads it)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host}, reload_on_update=False)
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
            step_id="zeroconf_confirm", data_schema=schema,  # type: ignore[arg-type, unused-ignore]
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
            # suggested, not default: an emptied field is left out of the
            # answer, and a default would bring the old folder back
            vol.Optional(
                CONF_CONFIG_DIR,
                description={"suggested_value": opts.get(CONF_CONFIG_DIR, "")},
            ): str,
            vol.Required(
                CONF_DEEP_SCAN,
                default=opts.get(CONF_DEEP_SCAN, DEFAULT_DEEP_SCAN),
            ): bool,
        })
        return self.async_show_form(step_id="init", data_schema=schema)  # type: ignore[arg-type, unused-ignore]
