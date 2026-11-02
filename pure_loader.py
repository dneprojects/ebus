"""Load the integration's Home-Assistant-free modules (entities, ids, strings_gen,
const) without running its ``__init__`` (which imports Home Assistant), for the
dev tools and the pure tests.

The package directory is registered as the bare package ``ebus_pure``, so the
modules' relative imports resolve among themselves. Do NOT put the package
directory itself on sys.path: its platform files would shadow stdlib modules
such as ``select``.
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PKG = ROOT / "custom_components" / "ebus"
_NAME = "ebus_pure"

try:  # the installed aioebus wins; a sibling checkout only stands in for none
    import aioebus  # noqa: F401
except ImportError:
    sys.path.insert(0, str(ROOT.parent / "aioebus" / "src"))


def pure(module: str) -> types.ModuleType:
    """Import ``custom_components/ebus/<module>.py`` as ``ebus_pure.<module>``."""
    if _NAME not in sys.modules:
        package = types.ModuleType(_NAME)
        package.__path__ = [str(PKG)]
        sys.modules[_NAME] = package
    return importlib.import_module(f"{_NAME}.{module}")
