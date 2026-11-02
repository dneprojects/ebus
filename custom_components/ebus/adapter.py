"""The adapter shield's own web API (eBUS Adapter Shield / ebusd-ESP)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)


async def fetch_status(hass: HomeAssistant, host: str, timeout: float = 5) -> dict[str, Any]:
    """Best-effort read of the adapter's status (id/MAC, firmware, Wi-Fi, uptime).
    Returns {} when the adapter has no web UI or is unreachable, so a plain
    serial/Pi adapter simply gets no extra info."""
    try:
        session = async_get_clientsession(hass)
        async with asyncio.timeout(timeout), session.get(f"http://{host}/api/v1/status") as resp:
            if resp.status != 200:
                return {}
            data = await resp.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, ValueError) as err:
        _LOGGER.debug("eBUS: adapter web status unavailable: %s", err)
        return {}
    return data if isinstance(data, dict) else {}


def wifi(status: dict[str, Any]) -> dict[str, Any]:
    """The station (Wi-Fi client) part of a status; {} when absent or malformed."""
    sta = status.get("sta")
    return sta if isinstance(sta, dict) else {}
