"""The engine's value cache on disk, so entities show their last value right
after a restart instead of being unavailable until the bus is read again."""
from __future__ import annotations

import logging
from typing import Final

from aioebus.engine import EbusEngine
from aioebus.values import CacheKey
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import CACHE_STORE, STORE_VERSION

_LOGGER = logging.getLogger(__name__)
# throttle for saving (below the shortest update interval: each update re-arms the
# delay, a longer one would never fire)
SAVE_DELAY: Final = 5
_JSON_SCALARS: Final = (str, int, float, bool, type(None))


def serialize(cache: dict[CacheKey, object]) -> dict[str, object]:
    """The value cache as JSON: [[["zz","name","label"], value], ...]. Only
    scalar values are kept, so the store always stays serialisable."""
    return {"values": [[list(key), value] for key, value in cache.items()
                       if isinstance(value, _JSON_SCALARS)]}


class CacheStore:
    """One entry's persisted value cache. It is written only once it has been
    read into the engine: a setup that fails before (a silent bus, a retry) must
    not overwrite the saved values with an empty cache."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, object]] = Store(
            hass, STORE_VERSION, f"{CACHE_STORE}{entry_id}")
        self.restored = False

    async def async_restore(self, engine: EbusEngine) -> None:
        """Seed the engine cache from the last saved values (the engine drops
        those of registers a scanned device no longer defines)."""
        self.restored = True
        try:
            stored = await self._store.async_load()
        except Exception as err:  # noqa: BLE001 - a bad cache file must not block setup
            _LOGGER.debug("eBUS: could not load value cache: %s", err)
            return
        values = stored.get("values") if stored else None
        if not isinstance(values, list):
            return
        engine.restore(
            ((str(item[0][0]), str(item[0][1]), str(item[0][2])), item[1])
            for item in values
            if (isinstance(item, list) and len(item) == 2
                and isinstance(item[0], list) and len(item[0]) == 3))
        _LOGGER.debug("eBUS: warm-started %d cached value(s)", len(engine.cache))

    def schedule_save(self, engine: EbusEngine) -> None:
        if self.restored:
            self._store.async_delay_save(lambda: serialize(engine.cache), SAVE_DELAY)

    async def async_save(self, engine: EbusEngine) -> None:
        if self.restored:
            await self._store.async_save(serialize(engine.cache))
