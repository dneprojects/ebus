"""The base of the entities that show bus values (not the adapter's own)."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Final

from aioebus.messages import MessageDef
from homeassistant.core import HassJob, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import EbusCoordinator

# seconds until a controller has recalculated what a change set in effect
SETTLE_S: Final = 5.0


class EbusBusEntity(CoordinatorEntity[EbusCoordinator]):
    """A value from the bus: unavailable once the link has been down too long
    for the last value to count as current (see EbusCoordinator.bus_stale)."""

    _attr_has_entity_name = True

    @property
    def available(self) -> bool:
        return super().available and not self.coordinator.bus_stale


class EbusSettlingEntity(EbusBusEntity):
    """A bus entity whose changes take effect on the controller with a delay:
    what a change touched is read at once, and what the controller derives from
    it again a few seconds later."""

    _reread: Callable[[], None] | None = None  # cancels the pending read
    _settling: tuple[MessageDef | None, ...] = ()

    async def _read_change(self, now: tuple[MessageDef | None, ...],
                           settled: tuple[MessageDef | None, ...]) -> None:
        await self.coordinator.async_read_now(*now)
        self._cancel_reread()
        if self.hass is not None and any(settled):
            self._settling = settled
            self._reread = async_call_later(self.hass, SETTLE_S, HassJob(
                self._read_settled, "ebus re-read after a change", cancel_on_shutdown=True))

    @callback
    def _read_settled(self, _now: datetime) -> None:
        self._reread = None
        self.hass.async_create_background_task(
            self.coordinator.async_read_now(*self._settling), "ebus re-read after a change")

    def _cancel_reread(self) -> None:
        if self._reread is not None:
            self._reread()
            self._reread = None

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_reread()
        await super().async_will_remove_from_hass()
