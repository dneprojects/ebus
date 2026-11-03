"""The base of the entities that show bus values (not the adapter's own)."""
from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import EbusCoordinator


class EbusBusEntity(CoordinatorEntity[EbusCoordinator]):
    """A value from the bus: unavailable once the link has been down too long
    for the last value to count as current (see EbusCoordinator.bus_stale)."""

    _attr_has_entity_name = True

    @property
    def available(self) -> bool:
        return super().available and not self.coordinator.bus_stale
