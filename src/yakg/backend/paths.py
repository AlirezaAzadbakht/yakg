"""XDG locations YAKG uses for its own files.

Kerio paths stay in ``constants.py``. These are user-owned and must be
injectable so tests never write under a real home directory.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def config_home(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the YAKG directory under ``XDG_CONFIG_HOME`` (default ``~/.config/yakg``)."""
    env = os.environ if environ is None else environ
    xdg = env.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "yakg"
    base = Path.home() if home is None else home
    return base / ".config" / "yakg"


def autostart_dir(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the XDG autostart directory (default ``~/.config/autostart``)."""
    env = os.environ if environ is None else environ
    xdg = env.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "autostart"
    base = Path.home() if home is None else home
    return base / ".config" / "autostart"


def data_home(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the applications directory under ``XDG_DATA_HOME``."""
    env = os.environ if environ is None else environ
    xdg = env.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "applications"
    base = Path.home() if home is None else home
    return base / ".local" / "share" / "applications"
