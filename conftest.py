"""Test setup: the installed aioebus is used (CI installs the version the manifest
requires); only when none is installed does a sibling source checkout stand in."""
import pathlib
import sys

try:
    import aioebus  # noqa: F401
except ImportError:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "aioebus" / "src"))
