"""Packaged files shipped with YAKG, including the application icon."""

from __future__ import annotations

from pathlib import Path

from yakg import APP_ID

ICON_FILENAME = f"{APP_ID}.svg"


def icon_theme_root() -> Path:
    """Directory that contains the ``hicolor`` icon theme tree."""
    return Path(__file__).resolve().parent / "icons"


def icon_path() -> Path:
    """Return the scalable application icon shipped in the package."""
    return icon_theme_root() / "hicolor" / "scalable" / "apps" / ICON_FILENAME
