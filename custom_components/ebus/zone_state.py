"""What applies in a heating zone right now, from the registers the aioebus
catalog names for it (see aioebus.catalog.ClimateZone).

Per the sensoCOMFORT manual (VRC 720/3, 0020334315_04, ch. 2.9/2.14.1): manual
heating holds one setpoint; time-controlled heating holds each time slot's
setpoint and, outside the slots, what the circuit's setback mode says (Eco: off,
frost protection only; Normal: the setback temperature). The controller's app
adds an away period (its own temperature), airing (heating off for 30 min) and a
quick veto, which overrides the setpoint for a while.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Final

from aioebus.catalog import ClimateZone
from aioebus.messages import MessageDef
from aioebus.values import CacheKey, value_key, values_of

OFF: Final = "off"
MANUAL: Final = "manual"
TIME_SLOT: Final = "time_slot"
SETBACK: Final = "setback"
ECO: Final = "eco"
TIME_CONTROLLED: Final = "time_controlled"  # the slot of today is not known
QUICK_VETO: Final = "quick_veto"
AWAY: Final = "away"
AIRING: Final = "airing"  # the app's Stosslueften: heating off for 30 min
SPECIAL: Final = "special"  # another special function (e.g. party)
STATES: Final = [OFF, MANUAL, TIME_SLOT, SETBACK, ECO, TIME_CONTROLLED,
                 QUICK_VETO, AWAY, AIRING, SPECIAL]

Data = Mapping[CacheKey, object]


def value_of(data: Data, msg: MessageDef | None, field: str | None = None) -> object:
    """A register's cached value: its one value, or the named field's."""
    if msg is None:
        return None
    values = values_of(msg)
    if field is not None:
        values = [v for v in values if v.field.name == field]
        if not values:
            return None
    key = values[0].cache_key if values else value_key(msg, msg.name)
    return data.get(key)


def in_use(data: Data, zone: ClimateZone) -> bool:
    """A zone is in use once one of its registers has a value."""
    return any(value_of(data, m) is not None
               for m in (zone.target_read, zone.current, zone.mode_read) if m is not None)


def messages_of(zone: ClimateZone) -> list[MessageDef]:
    """The reads the state is told from (to poll them)."""
    return [m for m in (zone.mode_read, zone.special_read, zone.holiday_start,
                        zone.holiday_end, zone.timer_frames, *zone.timer_days,
                        zone.setback_mode) if m is not None]


def _date(value: object) -> date | None:
    try:
        return datetime.strptime(str(value), "%d.%m.%Y").date()
    except ValueError:
        return None


def _minutes(value: object) -> int | None:
    # "hh:mm"; "24:00" ends a slot at midnight
    try:
        hours, minutes = (int(p) for p in str(value).split(":")[:2])
    except ValueError:
        return None
    return hours * 60 + minutes if 0 <= hours <= 24 and 0 <= minutes < 60 else None


def _in_slot(data: Data, zone: ClimateZone, now: datetime) -> bool | None:
    """Whether now lies in a time slot of today; None when that is not known
    (no program read yet, or outside the first of several slots: the catalog
    names only the first)."""
    day = now.weekday()
    if zone.timer_frames is None or len(zone.timer_days) != 7:
        return None
    count = value_of(data, zone.timer_frames, "slotcount" if day == 0 else f"slotcount_{day}")
    if not isinstance(count, int):
        return None
    if count == 0:
        return False
    slot = zone.timer_days[day]
    start = _minutes(value_of(data, slot, "htm"))
    end = _minutes(value_of(data, slot, "htm_1"))
    if count < 0 or start is None or end is None:
        return None
    if start <= now.hour * 60 + now.minute < end:
        return True
    return False if count == 1 else None


def zone_state(data: Data, zone: ClimateZone, now: datetime) -> str | None:
    """One of STATES, or None while the operating mode is unknown."""
    mode = value_of(data, zone.mode_read)
    if mode is None:
        return None
    if mode == zone.modes.get("off"):
        return OFF
    start = _date(value_of(data, zone.holiday_start))
    end = _date(value_of(data, zone.holiday_end))
    if start is not None and end is not None and start <= now.date() <= end:
        return AWAY
    special = value_of(data, zone.special_read)
    if special is not None and special == zone.special_values.get("veto"):
        return QUICK_VETO
    if special is not None and special == zone.special_values.get("ventilation"):
        return AIRING
    if special is not None and special != zone.special_values.get("none"):
        return SPECIAL
    if mode != zone.modes.get("auto"):
        return MANUAL
    slot = _in_slot(data, zone, now)
    if slot is None:
        return TIME_CONTROLLED
    if slot:
        return TIME_SLOT
    setback = value_of(data, zone.setback_mode)
    if setback is not None and setback == zone.setback_values.get("eco"):
        return ECO
    if setback is not None and setback == zone.setback_values.get("normal"):
        return SETBACK
    return TIME_CONTROLLED
