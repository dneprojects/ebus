"""The unique_id scheme of this integration's entities, in one place.

Every id starts with the config entry id (two adapters with devices on the same
addresses never collide). The entities, the polling scope and the registry
migrations all build or read ids through these helpers, so the scheme cannot
drift between them.
"""
from __future__ import annotations

from aioebus.engine import value_key
from aioebus.messages import MessageDef

CONTROL_SUFFIX = "_set"


def uid(prefix: str, *parts: object) -> str:
    return "_".join((prefix, *(str(p) for p in parts)))


def entity_uid(prefix: str, msg: MessageDef, label: str) -> str:
    """A register's sensor: '<prefix>_<zz>_<message>_<label>'."""
    return uid(prefix, *value_key(msg, label))


def control_uid(prefix: str, msg: MessageDef, label: str) -> str:
    """A register's number or select: its sensor id + '_set'."""
    return entity_uid(prefix, msg, label) + CONTROL_SUFFIX


def zone_uid(prefix: str, zz: str, n: int) -> str:
    """A heating zone's climate entity."""
    return uid(prefix, zz, f"z{n}", "climate")


def message_of(unique_id: str) -> str | None:
    """The message name inside a register entity's id, else None."""
    parts = unique_id.split("_", 3)  # <prefix>_<zz>_<message>_<label>[_set]
    return parts[2] if len(parts) == 4 else None
