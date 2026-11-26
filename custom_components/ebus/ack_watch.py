"""Acknowledgment watch: commands on the bus that did not get through.

The engine checks every telegram's acknowledgment (aioebus ``acks``): other
masters' passively, our own from the transaction. Here each fault to a device
on this bus becomes a logbook entry (at most one a minute per command and kind,
with the count since the last one). A single loss is bus noise the sender
repeats; a command lost several times in a row does not get through and raises
a repair issue, which clears itself once it arrives again.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Collection, Mapping
from typing import Any, Final

from aioebus.acks import ANSWER_REFUSED, NO_ACK, REFUSED, REPEATED, AckFault, CommandKey
from aioebus.frame import is_master
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .device import ADAPTER_NAME

_LOGGER = logging.getLogger(__name__)

EVENT_COMMAND_FAULT: Final = "ebus_command_fault"
ISSUE_KEY: Final = "commands_lost"
LOG_GAP_S: Final = 60.0  # one logbook entry a minute per command and kind
ISSUE_STREAK: Final = 3  # the same command lost this often in a row: an issue
# it clears when the command arrives again, or (no longer sent) after this long
# without a lost command to the device
ISSUE_CLEAR_S: Final = 1800.0

# what went wrong, per language (logbook text and the issue)
FAULT_TEXT: Final[dict[str, dict[str, str]]] = {
    "en": {NO_ACK: "not acknowledged", REFUSED: "refused",
           REPEATED: "taken only on the resend", ANSWER_REFUSED: "answer refused"},
    "de": {NO_ACK: "nicht quittiert", REFUSED: "abgelehnt",
           REPEATED: "erst bei Wiederholung angenommen", ANSWER_REFUSED: "Antwort abgelehnt"},
}
_LOST_TEXT: Final = {"en": "did not arrive", "de": "kam nicht an"}
_COUNT_TEXT: Final = {"en": "{n}x since the last entry", "de": "{n}x seit dem letzten Eintrag"}


def slave_of(addr: int) -> int:
    """The device address behind an address (a master's slave is 5 above it)."""
    return (addr + 5) & 0xFF if is_master(addr) else addr


def describe(data: Mapping[str, Any], lang: str) -> str:
    """The logbook text of one fault event."""
    lang = lang.split("-")[0]
    lang = lang if lang in FAULT_TEXT else "en"
    text = (f"{data['sender']} → {data['receiver']}: {data['message'] or data['command']} "
            f"{FAULT_TEXT[lang].get(data['fault'], data['fault'])}")
    if data["lost"]:
        text += f", {_LOST_TEXT[lang]}"
    if data["count"] > 1:
        text += f" ({_COUNT_TEXT[lang].format(n=data['count'])})"
    return text


class AckWatch:
    """Turns the engine's acknowledgment faults into logbook entries and repair
    issues, for the devices of one config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str,
                 devices: Callable[[], Collection[int]],
                 streaks: Callable[[], Mapping[CommandKey, int]]) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._devices = devices  # the scanned device addresses, read when needed
        self._streaks = streaks  # the engine's commands lost in a row, read when needed
        # per (receiver, command, kind): when it was last logged, faults since
        self._logged: dict[tuple[int, str, str], tuple[float, int]] = {}
        self._last_lost: dict[int, float] = {}  # per device: when a command was lost
        self._issues: set[int] = set()  # devices with a raised issue
        self._clear_stale_issues()

    def addresses(self) -> set[int]:
        """The addresses telegrams to a device on this bus are sent to: its
        slave address, and its master address when it has one."""
        out = set(self._devices())
        out |= {(a - 5) & 0xFF for a in out if is_master((a - 5) & 0xFF)}
        return out

    @callback
    def on_fault(self, fault: AckFault) -> None:
        """One fault from the engine (called in the event loop)."""
        if slave_of(fault.zz) not in self._devices():
            return  # a probe of an address without a device
        now = time.monotonic()
        self._log(fault, now)
        if fault.lost:
            self._last_lost[slave_of(fault.zz)] = now
            if fault.streak >= ISSUE_STREAK:
                self._raise(slave_of(fault.zz), fault)

    def _log(self, fault: AckFault, now: float) -> None:
        # an unknown message by its command only: the keys stay few
        key = (fault.zz, fault.message or fault.pbsb, fault.fault)
        last, count = self._logged.get(key, (-LOG_GAP_S, 0))
        count += 1
        if now - last < LOG_GAP_S:
            self._logged[key] = (last, count)
            return
        self._logged[key] = (now, 0)
        self.hass.bus.async_fire(EVENT_COMMAND_FAULT, {
            "config_entry_id": self.entry_id,
            "sender": ADAPTER_NAME if fault.own else self._name(fault.qq),
            "receiver": self._name(fault.zz), "own": fault.own,
            "sender_address": f"{fault.qq:02x}", "address": f"{fault.zz:02x}",
            "command": fault.pbsb, "data": fault.data.hex(), "message": fault.message,
            "fault": fault.fault, "lost": fault.lost, "count": count,
        })

    def _raise(self, zz: int, fault: AckFault) -> None:
        if zz in self._issues:
            return
        self._issues.add(zz)
        name = self._name(zz)
        sender = ADAPTER_NAME if fault.own else self._name(fault.qq)
        message = fault.message or fault.pbsb
        _LOGGER.warning("eBUS: %s from %s to %s (%02x) lost %d times in a row",
                        message, sender, name, zz, fault.streak)
        ir.async_create_issue(
            self.hass, DOMAIN, self._issue_id(zz), is_fixable=False,
            severity=ir.IssueSeverity.WARNING, translation_key=ISSUE_KEY,
            translation_placeholders={"device": name, "sender": sender,
                                      "message": message, "count": str(fault.streak)})

    @callback
    def tick(self) -> None:
        """Clear the issue of a device whose commands arrive again: none of them
        is lost in a row any more, or none was lost for a long time (the command
        is no longer sent)."""
        now = time.monotonic()
        stuck = {slave_of(key[1]) for key, n in self._streaks().items() if n >= ISSUE_STREAK}
        for zz in list(self._issues):
            if zz in stuck and now - self._last_lost.get(zz, now) < ISSUE_CLEAR_S:
                continue
            self._issues.discard(zz)
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(zz))
            _LOGGER.info("eBUS: commands to %s arrive again", self._name(zz))

    def close(self) -> None:
        """The entry unloads: its issues go with it (a new start sees afresh)."""
        for zz in self._issues:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(zz))
        self._issues.clear()

    def _clear_stale_issues(self) -> None:
        """Issues left from the last run (e.g. a restart while one was raised):
        this run counts afresh and raises them again if still so."""
        prefix = f"{ISSUE_KEY}_{self.entry_id}_"
        for domain, issue_id in list(ir.async_get(self.hass).issues):
            if domain == DOMAIN and issue_id.startswith(prefix):
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _issue_id(self, zz: int) -> str:
        return f"{ISSUE_KEY}_{self.entry_id}_{zz:02x}"

    def _name(self, addr: int) -> str:
        """The device's name in Home Assistant (as the user named it)."""
        zz = slave_of(addr)
        dev = dr.async_get(self.hass).async_get_device(identifiers={(DOMAIN, f"{zz:02x}")})
        if dev is not None:
            return dev.name_by_user or dev.name or f"{addr:#04x}"
        return f"{addr:#04x}"
