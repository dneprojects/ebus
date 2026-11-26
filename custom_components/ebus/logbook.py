"""Logbook: another master's command that did not get through (ack_watch.py)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback

from .ack_watch import EVENT_COMMAND_FAULT, describe
from .const import DOMAIN


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, Any]]], None],
) -> None:
    """Describe the command-fault events for the logbook."""

    @callback
    def _describe(event: Event) -> dict[str, Any]:
        return {"name": "eBUS", "message": describe(event.data, hass.config.language)}

    async_describe_event(DOMAIN, EVENT_COMMAND_FAULT, _describe)
