"""Sensor platform: one entity per decodable read field of every scanned device,
and per heating zone what applies in it now."""
from __future__ import annotations

from collections.abc import Callable
from typing import Final, cast

from aioebus.catalog import ClimateZone
from aioebus.codecs import has_numeric_value
from aioebus.values import Value, closes_period, values_of
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import EbusConfigEntry
from .adapter import wifi
from .coordinator import EbusCoordinator
from .device import adapter_device_info
from .entities import disabled_field_labels, icon_for, options_of, shown
from .entity import EbusBusEntity
from .ids import entity_uid, zone_state_uid
from .zone_state import STATES, in_use, zone_state

_UNIT_CLASS: Final[dict[str, tuple[SensorDeviceClass | None, SensorStateClass | None]]] = {
    "°C": (SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
    "bar": (SensorDeviceClass.PRESSURE, SensorStateClass.MEASUREMENT),
    "%": (None, SensorStateClass.MEASUREMENT),
    "kW": (SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
    "W": (SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
    "kWh": (SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
}


# the adapter's own diagnostic sensors (unique_id slug -- kept from the former
# German names --, translation key, value, unit, device class, state class, icon),
# attached to the adapter device alongside the connection sensor
_AdapterSpec = tuple[str, str, Callable[[EbusCoordinator], StateType], str | None,
                     SensorDeviceClass | None, SensorStateClass | None, str | None]


def _hours(value: object) -> StateType:
    return round(value / 3600, 1) if isinstance(value, int | float) else None


def _megabytes(value: object) -> StateType:
    return round(value / 1_000_000, 2) if isinstance(value, int | float) else None


# always available: derived from the engine / connection
_ADAPTER_SENSORS: Final[list[_AdapterSpec]] = [
    ("host", "adapter_host", lambda c: f"{c.host}:{c.port}",
     None, None, None, "mdi:ip-network"),
    ("telegramme", "adapter_telegrams",
     lambda c: c.engine.stats.telegrams if c.engine else None,
     None, None, SensorStateClass.TOTAL_INCREASING, "mdi:bus"),
    ("abfragen_beantwortet", "adapter_polls_answered",
     lambda c: c.engine.stats.polls_ok if c.engine else None,
     None, None, SensorStateClass.TOTAL_INCREASING, "mdi:check-network"),
    ("abfragen_ohne_antwort", "adapter_polls_unanswered",
     lambda c: c.engine.stats.polls_failed if c.engine else None,
     None, None, SensorStateClass.TOTAL_INCREASING, "mdi:network-off"),
    ("werte_zwischengespeichert", "adapter_cached_values",
     lambda c: len(c.engine.cache) if c.engine else None,
     None, None, SensorStateClass.MEASUREMENT, "mdi:database"),
]

# only when the adapter exposes its web API (eBUS Adapter Shield / ebusd-ESP).
# uptime and free memory are converted to h / MB up front (no state class, so
# the unit is just a display unit and HA raises no "unit changed" statistics flag)
_ADAPTER_WEB_SENSORS: Final[list[_AdapterSpec]] = [
    ("wlan_signal", "adapter_wifi_signal",
     lambda c: cast(StateType, wifi(c.adapter_status).get("rssi")), "dBm",
     SensorDeviceClass.SIGNAL_STRENGTH, SensorStateClass.MEASUREMENT, None),
    ("laufzeit", "adapter_uptime", lambda c: _hours(c.adapter_status.get("uptime")),
     UnitOfTime.HOURS, SensorDeviceClass.DURATION, None, "mdi:timer-outline"),
    ("freier_speicher", "adapter_free_memory",
     lambda c: _megabytes(c.adapter_status.get("heap")),
     UnitOfInformation.MEGABYTES, SensorDeviceClass.DATA_SIZE, None, "mdi:memory"),
]


PARALLEL_UPDATES = 0  # the coordinator updates; the engine queues writes


async def async_setup_entry(
    hass: HomeAssistant, entry: EbusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        EbusSensor(coordinator, field)
        for msg in coordinator.read_messages
        for field in values_of(msg)
    ]
    entities += [EbusAdapterSensor(coordinator, *spec) for spec in _ADAPTER_SENSORS]
    async_add_entities(entities)

    # an adapter with a web UI adds its own metrics, also once its web API
    # answers later (it may have been slow at setup)
    web_added = False

    @callback
    def _add_web_sensors() -> None:
        nonlocal web_added
        if not web_added and coordinator.adapter_status:
            web_added = True
            async_add_entities(
                EbusAdapterSensor(coordinator, *spec) for spec in _ADAPTER_WEB_SENSORS)

    _add_web_sensors()
    entry.async_on_unload(coordinator.async_add_listener(_add_web_sensors))

    # per-device Vaillant identification (article/serial), static diagnostic
    # values; one read later at runtime adds its sensors then
    identified: set[int] = set()

    @callback
    def _add_ident_sensors() -> None:
        new: list[SensorEntity] = []
        for zz, ident in coordinator.idents.items():
            if zz in identified:
                continue
            identified.add(zz)
            new.append(EbusIdentSensor(
                coordinator, zz, "artikelnummer", "ident_article", ident.article, "mdi:barcode"))
            new.append(EbusIdentSensor(
                coordinator, zz, "seriennummer", "ident_serial", ident.serial, "mdi:identifier"))
            if ident.year:
                week = f" KW{ident.week:02d}" if ident.week else ""
                new.append(EbusIdentSensor(
                    coordinator, zz, "baujahr", "ident_manufactured",
                    f"{ident.year}{week}", "mdi:calendar"))
        if new:
            async_add_entities(new)

    _add_ident_sensors()
    entry.async_on_unload(coordinator.async_add_listener(_add_ident_sensors))

    # what applies in a heating zone now; added with the zone's thermostat, once
    # one of its registers has a value
    pending = coordinator.catalog.zones()

    @callback
    def _add_zone_states() -> None:
        active = [z for z in pending if in_use(coordinator.data, z)]
        if active:
            for zone in active:
                pending.remove(zone)
            async_add_entities(EbusZoneStateSensor(coordinator, z) for z in active)

    _add_zone_states()
    if pending:
        entry.async_on_unload(coordinator.async_add_listener(_add_zone_states))


class EbusSensor(EbusBusEntity, SensorEntity):
    """A single decoded eBUS register value."""

    def __init__(self, coordinator: EbusCoordinator, field: Value) -> None:
        super().__init__(coordinator)
        self._field = field
        self._key = field.cache_key
        zz = coordinator.catalog.owner(field.msg)
        self._attr_unique_id = entity_uid(coordinator.uid_prefix, field.msg, field.label)
        self._attr_translation_key = field.key
        self._attr_icon = icon_for(field.field)
        # channels known to be unconnected on this device type (from the def
        # metadata, so the decision is static at registration) are off by default
        definition = coordinator.catalog.definition(zz)
        dead = disabled_field_labels(definition)
        # an unreviewed definition (user file, CDN) brings every register: its
        # read-on-demand ones (plain 'r', which ebusd does not poll either) start
        # disabled, so a new device does not flood the bus; polled and passive
        # ones show up at once
        on_demand = (definition is not None and definition.source != "bundled"
                     and field.msg.direction == "r")
        self._attr_entity_registry_enabled_default = field.label not in dead and not on_demand

        if field.field.values:  # value table -> enum sensor with its labels
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = options_of(field.field)
        else:
            unit = field.field.unit
            if unit:
                self._attr_native_unit_of_measurement = unit
            dev_cls, unit_state = _UNIT_CLASS.get(unit, (None, None))
            self._attr_device_class = dev_cls
            # a numeric reading gets long-term statistics: energy accumulates
            # (total_increasing), everything else is a measurement; text, date/
            # time and flag fields stay without a state class
            if has_numeric_value(field.field.type):
                state = unit_state or SensorStateClass.MEASUREMENT
                if state is SensorStateClass.TOTAL_INCREASING and closes_period(field.msg):
                    # a closed period's total is a fixed figure, no running
                    # counter: as total (no last_reset) its history keeps every
                    # period's figure and stays compatible with statistics
                    # recorded before; its sum is no energy-dashboard source
                    state = SensorStateClass.TOTAL
                self._attr_state_class = state

        self._attr_device_info = coordinator.device_info(zz)

    @property
    def native_value(self) -> StateType:
        value = self.coordinator.data.get(self._key)
        # an ENUM sensor must only report one of its declared options; _attr_options
        # is set only for those and raises when unset (HA cached-property backing),
        # so read it defensively -- a non-enum sensor has no options to enforce
        options = getattr(self, "_attr_options", None)
        if options is not None and value not in options:
            return None
        return cast(StateType, shown(value))

    @property
    def available(self) -> bool:
        return super().available and self._key in self.coordinator.data


class EbusZoneStateSensor(EbusBusEntity, SensorEntity):
    """What applies in a heating zone now: off, manual, a time slot, setback,
    Eco, quick veto, away (see zone_state)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATES
    _attr_translation_key = "zone_state"
    _attr_icon = "mdi:home-thermometer-outline"

    def __init__(self, coordinator: EbusCoordinator, zone: ClimateZone) -> None:
        super().__init__(coordinator)
        self._zone = zone
        self._attr_translation_placeholders = {"n": str(zone.n)}
        self._attr_unique_id = zone_state_uid(coordinator.uid_prefix, zone.zz, zone.n)
        self._attr_device_info = coordinator.device_info(int(zone.zz, 16))

    @property
    def native_value(self) -> StateType:
        return zone_state(self.coordinator.data, self._zone, dt_util.now())


class EbusAdapterSensor(CoordinatorEntity[EbusCoordinator], SensorEntity):
    """A diagnostic reading about the adapter link itself (not a bus register)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: EbusCoordinator,
        slug: str,
        key: str,
        value: Callable[[EbusCoordinator], StateType],
        unit: str | None,
        device_class: SensorDeviceClass | None,
        state_class: SensorStateClass | None,
        icon: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._value = value
        self._attr_translation_key = key
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        if icon is not None:
            self._attr_icon = icon
        self._attr_unique_id = coordinator.uid("adapter", slug)
        self._attr_device_info = adapter_device_info()

    @property
    def native_value(self) -> StateType:
        return self._value(self.coordinator)


class EbusIdentSensor(CoordinatorEntity[EbusCoordinator], SensorEntity):
    """A static identification value (article/serial/year) read once at setup."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: EbusCoordinator, zz: int, slug: str, key: str, value: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._val = value
        self._attr_translation_key = key
        self._attr_icon = icon
        self._attr_unique_id = coordinator.uid(f"{zz:02x}", slug)
        self._attr_device_info = coordinator.device_info(zz)

    @property
    def native_value(self) -> StateType:
        return self._val
