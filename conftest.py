"""Test setup: the installed aioebus is used (CI installs the version the manifest
requires); only when none is installed does a sibling source checkout stand in
(see pure_loader)."""
import pure_loader  # noqa: F401
