"""Config entry migrations: bring the registry of an entry from an older version
up to date, once per step (MINOR_VERSION in config_flow.py)."""
from __future__ import annotations

import re
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import DOMAIN
from .device import ADAPTER_IDENTIFIER
from .entities import bundled_control_class
from .ids import CONTROL_SUFFIX, message_of

# Registry clean-up of minor version 2. Retired: another register carries the same
# value (one home per value) -- the b51a lifetime yields and TotalEnergyUsage
# duplicate the b516/10 float counters, CurrentConsumedPower and
# RunDataElectricPowerConsumption duplicate PowerConsumptionHmu. Renamed: the 0.2.0
# names of the b516/10 counters, and SupplyTempWeighted, a second definition of
# TargetFlowTemp's register.
RETIRED_MESSAGES: Final[frozenset[str]] = frozenset({
    "YieldHc", "YieldHwc", "YieldCooling", "TotalEnergyUsage",
    "CurrentConsumedPower", "RunDataElectricPowerConsumption",
})
RENAMED_MESSAGES: Final[dict[str, str]] = {
    "EnergyEnvironmentHeat": "EnergyEnvironmentHc",
    "EnergyEnvironmentWater": "EnergyEnvironmentHwc",
    "EnergyYieldHeat": "EnergyGeneratedHc",
    "EnergyYieldWater": "EnergyGeneratedHwc",
    "EnergyYieldCool": "EnergyGeneratedCool",
    "EnergyElectricHeat": "EnergyElectricHc",
    "EnergyElectricWater": "EnergyElectricHwc",
    "SupplyTempWeighted": "TargetFlowTemp",
}

# heating-circuit limits moved from user to configuration in 1.6, cooling in 1.7
_BECAME_INSTALLER_1_6 = frozenset(
    f"Hc{n}{name}" for n in "123"
    for name in ("SummerTempLimit", "MinFlowTempDesired", "MaxFlowTempDesired"))
_BECAME_INSTALLER_1_7 = frozenset(f"Hc{n}MinCoolingTempDesired" for n in "123")


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Bring an entry from an older version up to date (runs once per step)."""
    if entry.version > 1:
        return False  # downgrade from a future version
    if entry.minor_version < 2:
        _migrate_retired_and_renamed(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=2)
    if entry.minor_version < 3:
        _migrate_nameless_entity_ids(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=3)
    if entry.minor_version < 4:
        _migrate_scoped_ids(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=4)
    if entry.minor_version < 5:
        await hass.async_add_executor_job(_load_definitions)
        _migrate_control_classes(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=5)
    if entry.minor_version < 6:
        await hass.async_add_executor_job(_load_definitions)
        _migrate_control_classes(hass, entry, _BECAME_INSTALLER_1_6)
        hass.config_entries.async_update_entry(entry, minor_version=6)
    if entry.minor_version < 7:
        await hass.async_add_executor_job(_load_definitions)
        _migrate_control_classes(hass, entry, _BECAME_INSTALLER_1_7)
        hass.config_entries.async_update_entry(entry, minor_version=7)
    return True


def _load_definitions() -> None:
    """Read the bundled definition files (blocking; run in an executor)."""
    bundled_control_class("")


def _migrate_control_classes(hass: HomeAssistant, entry: ConfigEntry,
                             only: frozenset[str] | None = None) -> None:
    """1.4 -> 1.5: a writable register becomes a control only by its class
    (presentation.json 'controls'). Switch off configuration ones that used to be
    created enabled; the user can switch them on again. Controls a register no
    longer gets are dropped at setup, where the device's own definition is known.
    Later steps pass the registers they moved to configuration (``only``), so
    configuration controls the user enabled since stay on."""
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if ent.domain not in ("number", "select") or not ent.unique_id.endswith(CONTROL_SUFFIX):
            continue
        message = message_of(ent.unique_id)
        if message is None or (only is not None and message not in only):
            continue
        if bundled_control_class(message) == "installer" and ent.disabled_by is None:
            registry.async_update_entity(
                ent.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION)


_ADAPTER_UID = re.compile(r"^ebus_(?![0-9a-f]{2}_)(?P<host>.+)_(?P<port>\d+)_(?P<rest>.+)$")


def _migrate_scoped_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """1.3 -> 1.4: unique_ids start with the entry id instead of 'ebus' (two
    adapters with devices on the same addresses never collide), and the adapter's
    own entities and device are keyed independently of its address (a new IP
    keeps them). Entity ids, and so history, stay as they are."""
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        uid = ent.unique_id
        if (m := _ADAPTER_UID.match(uid)) is not None:
            new = f"{entry.entry_id}_adapter_{m['rest']}"
        elif uid.startswith("ebus_"):
            new = f"{entry.entry_id}_{uid[len('ebus_'):]}"
        else:
            continue
        if registry.async_get_entity_id(ent.domain, DOMAIN, new):
            registry.async_remove(ent.entity_id)
        else:
            registry.async_update_entity(ent.entity_id, new_unique_id=new)
    devices = dr.async_get(hass)
    old = (DOMAIN, f"{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}")
    adapter = devices.async_get_device_by_identifier(old, entry.entry_id)
    if adapter is not None and devices.async_get_device_by_identifier(
            ADAPTER_IDENTIFIER, entry.entry_id) is None:
        devices.async_update_device(adapter.id, new_identifiers={ADAPTER_IDENTIFIER})


def _migrate_nameless_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """1.2 -> 1.3: an entity registered while its translation had no name (a 'uw'
    sensor before 0.3.1) got the bare device slug as entity_id; append its label."""
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    areas = ar.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        parts = ent.unique_id.split("_", 3)
        if len(parts) != 4 or parts[0] != "ebus" or ent.device_id is None:
            continue
        device = devices.async_get(ent.device_id)
        if device is None:
            continue
        # the id HA derived from the device name alone, possibly area-prefixed
        area = areas.async_get_area(device.area_id) if device.area_id else None
        names = {n for n in (device.name_by_user, device.name) if n}
        bare = {f"{ent.domain}.{slugify(n)}" for n in names}
        if area is not None:
            bare |= {f"{ent.domain}.{slugify(f'{area.name} {n}')}" for n in names}
        new_id = f"{ent.entity_id}_{slugify(parts[3])}"
        if ent.entity_id in bare and registry.async_get(new_id) is None:
            registry.async_update_entity(ent.entity_id, new_entity_id=new_id)


def retired_unique_id(unique_id: str) -> bool:
    """True for a sensor unique_id ('ebus_<zz>_<message>_<label>') whose message
    was retired from the bundled definitions (see RETIRED_MESSAGES)."""
    parts = unique_id.split("_", 3)
    return len(parts) == 4 and parts[0] == "ebus" and parts[2] in RETIRED_MESSAGES


def renamed_unique_id(unique_id: str) -> str | None:
    """The current unique_id for one whose message was renamed (see
    RENAMED_MESSAGES), else None. The label starts with the message name."""
    parts = unique_id.split("_", 3)
    if len(parts) != 4 or parts[0] != "ebus" or parts[2] not in RENAMED_MESSAGES:
        return None
    old, new = parts[2], RENAMED_MESSAGES[parts[2]]
    label = new + parts[3][len(old):] if parts[3].startswith(old) else parts[3]
    return f"ebus_{parts[1]}_{new}_{label}"


def _migrate_retired_and_renamed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """1.1 -> 1.2: drop the entities of retired messages and move renamed ones to
    their new unique_id, so they keep their entity_id and history. Where the
    successor already exists (created by 0.3.0), the old entry is the orphan."""
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if retired_unique_id(ent.unique_id):
            registry.async_remove(ent.entity_id)
            continue
        new_uid = renamed_unique_id(ent.unique_id)
        if new_uid is None:
            continue
        if registry.async_get_entity_id(ent.domain, DOMAIN, new_uid):
            registry.async_remove(ent.entity_id)
        else:
            registry.async_update_entity(ent.entity_id, new_unique_id=new_uid)
