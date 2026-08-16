"""User preferences for YAKG itself, not for Kerio.

Stored next to profiles as ``preferences.json``. This is a separate file
from ``state.json`` because activating a profile rewrites that file.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yakg.backend.config import FILE_MODE, write_private_file
from yakg.backend.paths import config_home
from yakg.utils.logging import get_logger

log = get_logger(__name__)

PREFERENCES_NAME = "preferences.json"


@dataclass(frozen=True)
class Preferences:
    """Session behaviour that is not a VPN profile."""

    start_at_login: bool = False
    notifications_enabled: bool = True


def preferences_path(root: Path | None = None) -> Path:
    """Return the preferences file under ``root`` or the XDG config home."""
    return (root if root is not None else config_home()) / PREFERENCES_NAME


def load_preferences(root: Path | None = None) -> Preferences:
    """Load preferences, or the defaults when the file is missing."""
    path = preferences_path(root)
    if not path.is_file():
        return Preferences()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.info("Could not read preferences; using defaults")
        return Preferences()
    if not isinstance(loaded, dict):
        return Preferences()
    return Preferences(
        start_at_login=_as_bool(loaded.get("start_at_login"), False),
        notifications_enabled=_as_bool(loaded.get("notifications_enabled"), True),
    )


def save_preferences(preferences: Preferences, root: Path | None = None) -> None:
    """Atomically write ``preferences``. Never writes ``state.json``."""
    directory = root if root is not None else config_home()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / PREFERENCES_NAME
    payload = {
        "start_at_login": preferences.start_at_login,
        "notifications_enabled": preferences.notifications_enabled,
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(f"{path.name}.tmp")
    write_private_file(tmp, text.encode("utf-8"))
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        os.chmod(path, FILE_MODE)
    log.info(
        "Saved preferences start_at_login=%s notifications=%s",
        preferences.start_at_login,
        preferences.notifications_enabled,
    )


def _as_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default
