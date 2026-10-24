"""Calendar platform: Vaillant weekly time programs.

Convention-based, with no declaration needed: registers named
'<prefix>Timer_<Weekday><slot>' (field htm = from, htm_1 = to, slottemp =
temperature) form one weekly program per prefix. The bundling comes straight
from the register names in the definition. Read-only -- this controller exposes
no writable timer message, so the schedule is shown, not edited."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EbusConfigEntry
from .coordinator import EbusCoordinator
from .device import device_info
from .library import TimerSlot, timer_schedules


def _hm(value: object) -> time | None:
    if isinstance(value, str) and ":" in value:
        try:
            hour, minute = value.split(":")[:2]
            return time(int(hour), int(minute))
        except ValueError:
            return None
    return None


async def async_setup_entry(
    hass: HomeAssistant, entry: EbusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    schedules = timer_schedules(coordinator.devices, coordinator.user_dir)
    async_add_entities(
        EbusCalendar(coordinator, zz, prefix, slots)
        for (zz, prefix), slots in schedules.items()
    )


class EbusCalendar(CoordinatorEntity[EbusCoordinator], CalendarEntity):
    """One weekly time program (e.g. hot water or a heating zone)."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: EbusCoordinator, zz: str, prefix: str, slots: list[TimerSlot]
    ) -> None:
        super().__init__(coordinator)
        self._zz = zz
        self._prefix = prefix
        self._by_day: dict[int, list[TimerSlot]] = {}
        for slot in slots:
            self._by_day.setdefault(slot.weekday, []).append(slot)
        for day_slots in self._by_day.values():
            day_slots.sort(key=lambda s: s.slot)
        self._attr_name = f"{prefix} Zeitprogramm"
        self._attr_unique_id = f"ebus_{zz}_{prefix}_timer".lower()
        self._attr_device_info = device_info(int(zz, 16), coordinator.devices.get(int(zz, 16)))

    def _field(self, slot: TimerSlot, short: str) -> object:
        # multi-field message -> cache field is "<message name> <field>"
        return self.coordinator.data.get((self._zz, slot.msg.name, f"{slot.msg.name} {short}"))

    def _events_for_day(self, day: date) -> list[CalendarEvent]:
        events: list[CalendarEvent] = []
        tz = dt_util.get_default_time_zone()
        for slot in self._by_day.get(day.weekday(), []):
            frm = _hm(self._field(slot, "htm"))
            to = _hm(self._field(slot, "htm_1"))
            if frm is None or to is None or frm == to:
                continue  # empty / invalid slot
            start = datetime.combine(day, frm, tzinfo=tz)
            end = datetime.combine(day, to, tzinfo=tz)
            if end <= start:
                end += timedelta(days=1)  # window crosses midnight
            try:
                summary = f"{float(self._field(slot, 'slottemp')):g} °C"  # type: ignore[arg-type]
            except (TypeError, ValueError):
                summary = "on"
            events.append(CalendarEvent(
                start=start, end=end, summary=summary,
                uid=f"{self._zz}|{self._prefix}|{slot.weekday}|{slot.slot}|{day.isoformat()}"))
        return events

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        events: list[CalendarEvent] = []
        day = start_date.date()
        while day <= end_date.date():
            events.extend(self._events_for_day(day))
            day += timedelta(days=1)
        return events

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        upcoming: list[CalendarEvent] = []
        for offset in range(8):
            upcoming.extend(self._events_for_day((now + timedelta(days=offset)).date()))
        upcoming.sort(key=lambda ev: ev.start)
        return next((ev for ev in upcoming if ev.end > now), None)
