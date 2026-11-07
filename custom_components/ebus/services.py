"""The integration's actions: read any register of a bus device, for finding out
what a device offers. It only reads; through the running connection, so it keeps
the bus timing the adapter needs."""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final

import voluptuous as vol
from aioebus.active import ReadError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

SERVICE_READ_REGISTER: Final = "read_register"
ATTR_ENTRY: Final = "config_entry_id"
ATTR_ADDRESS: Final = "address"
ATTR_COMMAND: Final = "command"
ATTR_DATA: Final = "data"


def _hex(length: int | None = None) -> Callable[[object], str]:
    """A hex string, whole bytes, of that many characters if given."""
    def check(value: object) -> str:
        text = str(value).strip().lower()
        if (not re.fullmatch(r"([0-9a-f]{2})*", text)
                or (length is not None and len(text) != length)):
            raise vol.Invalid(f"{value!r}: expected hex digits"
                              + (f" ({length})" if length else ""))
        return text
    return check


READ_SCHEMA: Final = vol.Schema({
    vol.Required(ATTR_ENTRY): cv.string,
    vol.Required(ATTR_ADDRESS): _hex(2),
    vol.Required(ATTR_COMMAND): _hex(4),
    vol.Optional(ATTR_DATA, default=""): _hex(),
})


async def _read_register(call: ServiceCall) -> ServiceResponse:
    entry = call.hass.config_entries.async_get_entry(call.data[ATTR_ENTRY])
    if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError("This eBUS adapter is not set up or not running")
    engine = entry.runtime_data.engine
    if engine is None:
        raise HomeAssistantError("The eBUS adapter is not connected")
    try:
        answer = await engine.read_raw(int(call.data[ATTR_ADDRESS], 16), call.data[ATTR_COMMAND],
                                       call.data[ATTR_DATA])
    except ValueError as err:  # no slave address
        raise ServiceValidationError(str(err)) from err
    except (ReadError, TimeoutError) as err:
        raise HomeAssistantError(f"No answer: {err}") from err
    return {"answer": answer.hex()}


def async_setup_services(hass: HomeAssistant) -> None:
    hass.services.async_register(DOMAIN, SERVICE_READ_REGISTER, _read_register,
                                 schema=READ_SCHEMA,
                                 supports_response=SupportsResponse.ONLY)
