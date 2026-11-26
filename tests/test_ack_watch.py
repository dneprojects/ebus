"""Other masters' commands that did not get through: logbook events (throttled),
the repair issue and its clearing (run under pytest-homeassistant-custom-component)."""
from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from aioebus.acks import NO_ACK, REFUSED, REPEATED, AckFault
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.ebus import ack_watch as aw
from custom_components.ebus.ack_watch import EVENT_COMMAND_FAULT, AckWatch, describe
from custom_components.ebus.const import DOMAIN

CTL, VR71 = 0x15, 0x26  # the controller sends as master 0x10


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


def _fault(kind: str, zz: int = VR71, message: str | None = "SetActorState") -> AckFault:
    return AckFault(0.0, 0x10, zz, "b510", bytes.fromhex("0100"), kind, message)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _watch(hass: HomeAssistant, clock: _Clock) -> AckWatch:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999})
    entry.add_to_hass(hass)
    reg = dr.async_get(hass)
    for zz, name in ((CTL, "sensoCOMFORT"), (VR71, "VR 71")):
        reg.async_get_or_create(config_entry_id=entry.entry_id,
                                identifiers={(DOMAIN, f"{zz:02x}")}, name=name)
    with patch.object(aw.time, "monotonic", clock):
        return AckWatch(hass, entry.entry_id, lambda: {CTL, VR71})


async def test_each_fault_is_logged_once_a_minute_with_the_count(hass: HomeAssistant):
    clock = _Clock()
    watch = await _watch(hass, clock)
    events = async_capture_events(hass, EVENT_COMMAND_FAULT)
    with patch.object(aw.time, "monotonic", clock):
        watch.on_fault(_fault(NO_ACK))
        for _ in range(3):  # within the minute: counted, not logged
            clock.now += 10
            watch.on_fault(_fault(NO_ACK))
        watch.on_fault(_fault(REPEATED))  # another kind: its own entry
        watch.on_fault(_fault(NO_ACK, zz=0x0B))  # no device there: ignored
        clock.now += 60
        watch.on_fault(_fault(NO_ACK))
    await hass.async_block_till_done()
    got = [(e.data["fault"], e.data["count"]) for e in events]
    assert got == [(NO_ACK, 1), (REPEATED, 1), (NO_ACK, 4)]
    first = events[0].data
    assert (first["sender"], first["receiver"], first["lost"]) == ("sensoCOMFORT", "VR 71", True)
    assert describe(first, "de") == "sensoCOMFORT → VR 71: SetActorState nicht quittiert, kam nicht an"
    assert describe(events[2].data, "en-GB").endswith("did not arrive (4x since the last entry)")


async def test_lost_commands_raise_an_issue_that_clears_itself(hass: HomeAssistant):
    clock = _Clock()
    watch = await _watch(hass, clock)
    issues = ir.async_get(hass)
    issue_id = f"commands_lost_{watch.entry_id}_26"
    with patch.object(aw.time, "monotonic", clock):
        for _ in range(4):
            watch.on_fault(_fault(REFUSED))
            watch.on_fault(_fault(REPEATED))  # arrived after all: never counts
            clock.now += 30
        assert issues.async_get_issue(DOMAIN, issue_id) is None
        watch.on_fault(_fault(REFUSED))
        issue = issues.async_get_issue(DOMAIN, issue_id)
        assert issue is not None
        assert issue.translation_placeholders["device"] == "VR 71"
        assert issue.translation_placeholders["count"] == "5"
        clock.now += aw.ISSUE_CLEAR_S - 1
        watch.tick()  # not long enough without a lost one
        assert issues.async_get_issue(DOMAIN, issue_id) is not None
        clock.now += 2
        watch.tick()
    assert issues.async_get_issue(DOMAIN, issue_id) is None


async def test_lost_commands_spread_out_raise_nothing(hass: HomeAssistant):
    clock = _Clock()
    watch = await _watch(hass, clock)
    with patch.object(aw.time, "monotonic", clock):
        for _ in range(10):  # one every 3 min: never 5 within 10 min
            watch.on_fault(_fault(NO_ACK))
            clock.now += 180
    assert not [i for d, i in ir.async_get(hass).issues if d == DOMAIN]


async def test_an_issue_from_the_last_run_is_cleared_at_start(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "port": 9999})
    entry.add_to_hass(hass)
    for issue_id in (f"commands_lost_{entry.entry_id}_26", "commands_lost_other_26"):
        ir.async_create_issue(hass, DOMAIN, issue_id, is_fixable=False,
                              severity=ir.IssueSeverity.WARNING, translation_key="commands_lost")
    watch = AckWatch(hass, entry.entry_id, lambda: {VR71})
    left = {i for d, i in ir.async_get(hass).issues if d == DOMAIN}
    assert left == {"commands_lost_other_26"}  # another entry's stays
    assert watch.addresses() == {VR71}  # 0x21 is no master address


async def test_master_addresses_of_devices_count_too(hass: HomeAssistant):
    watch = AckWatch(hass, "x", lambda: {CTL, 0x08})
    assert watch.addresses() == {CTL, 0x10, 0x08, 0x03}


async def test_our_own_lost_command_is_logged_as_the_adapters(hass: HomeAssistant):
    clock = _Clock()
    watch = await _watch(hass, clock)
    events = async_capture_events(hass, EVENT_COMMAND_FAULT)
    own = AckFault(0.0, 0x31, CTL, "b524", bytes.fromhex("02000300"), NO_ACK, "Z1DayTemp",
                   own=True)
    with patch.object(aw.time, "monotonic", clock):
        watch.on_fault(own)
    await hass.async_block_till_done()
    data = events[0].data
    assert (data["sender"], data["receiver"], data["own"]) == ("eBUS Adapter", "sensoCOMFORT", True)
