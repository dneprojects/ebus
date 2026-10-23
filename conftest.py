"""Test setup: make aioebus importable from a sibling source checkout when it is
not installed (in CI it is installed as a dependency, so this is a no-op)."""
import pathlib
import sys

_aioebus_src = pathlib.Path(__file__).resolve().parent.parent / "aioebus" / "src"
if _aioebus_src.is_dir() and str(_aioebus_src) not in sys.path:
    sys.path.insert(0, str(_aioebus_src))
