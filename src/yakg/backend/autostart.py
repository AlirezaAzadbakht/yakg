"""XDG autostart and a user applications desktop file.

Enabling login startup writes ``yakg --hidden``. It does not start the
Kerio service. Paths are injectable so tests never touch a real home.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from yakg import APP_ID, APP_NAME
from yakg.backend.paths import autostart_dir, data_home, icons_home
from yakg.backend.preferences import load_preferences, save_preferences
from yakg.models.action import ActionOutcome
from yakg.resources import ICON_FILENAME, icon_path
from yakg.utils.logging import get_logger

log = get_logger(__name__)

DESKTOP_FILENAME = f"{APP_ID}.desktop"

_APPLICATIONS_DESKTOP = f"""\
[Desktop Entry]
Type=Application
Name={APP_NAME}
GenericName=Kerio VPN Client
Comment=Manage the Kerio Control VPN Client
Exec=yakg
Icon={APP_ID}
Terminal=false
Categories=Network;GTK;
Keywords=VPN;Kerio;Network;
StartupNotify=true
StartupWMClass={APP_ID}
X-GNOME-UsesNotifications=true
"""

_AUTOSTART_DESKTOP = f"""\
[Desktop Entry]
Type=Application
Name={APP_NAME}
GenericName=Kerio VPN Client
Comment=Manage the Kerio Control VPN Client
Exec=yakg --hidden
Icon={APP_ID}
Terminal=false
Categories=Network;GTK;
Keywords=VPN;Kerio;Network;
StartupNotify=false
StartupWMClass={APP_ID}
X-GNOME-Autostart-enabled=true
X-GNOME-UsesNotifications=true
"""


def applications_desktop_text() -> str:
    """Desktop file for ``~/.local/share/applications`` (notification identity)."""
    return _APPLICATIONS_DESKTOP


def autostart_desktop_text() -> str:
    """Desktop file for XDG autostart. Must launch hidden."""
    return _AUTOSTART_DESKTOP


def enable_autostart(directory: Path | None = None) -> Path:
    """Write the autostart desktop file. Returns the path written."""
    folder = directory if directory is not None else autostart_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / DESKTOP_FILENAME
    _write_text(path, autostart_desktop_text())
    log.info("Enabled YAKG autostart")
    return path


def disable_autostart(directory: Path | None = None) -> None:
    """Remove the autostart desktop file if it is present."""
    folder = directory if directory is not None else autostart_dir()
    path = folder / DESKTOP_FILENAME
    if path.is_file():
        path.unlink()
    log.info("Disabled YAKG autostart")


def autostart_enabled(directory: Path | None = None) -> bool:
    """True when the autostart file exists and is not marked Hidden."""
    folder = directory if directory is not None else autostart_dir()
    path = folder / DESKTOP_FILENAME
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if "Hidden=true" in text:
        return False
    return "yakg" in text and "--hidden" in text


def ensure_application_icon(directory: Path | None = None) -> Path:
    """Copy the packaged icon into the user hicolor theme if needed."""
    folder = directory if directory is not None else icons_home()
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / ICON_FILENAME
    source = icon_path()
    if source.is_file() and (
        not destination.is_file() or destination.read_bytes() != source.read_bytes()
    ):
        destination.write_bytes(source.read_bytes())
        log.info("Installed the application icon")
    return destination


def ensure_application_desktop(directory: Path | None = None) -> Path:
    """Write the applications desktop file when it is missing or stale."""
    folder = directory if directory is not None else data_home()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / DESKTOP_FILENAME
    text = applications_desktop_text()
    if not path.is_file() or _desktop_needs_refresh(path):
        _write_text(path, text)
        log.info("Installed the user applications desktop file")
    ensure_application_icon()
    return path


def _desktop_needs_refresh(path: Path) -> bool:
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        return True
    if f"Icon={APP_ID}" in existing:
        return False
    return "Name=YAKG" in existing and "Icon=network-vpn" in existing


def apply_start_at_login(
    enabled: bool,
    *,
    config_root: Path | None = None,
    autostart: Path | None = None,
    applications: Path | None = None,
) -> ActionOutcome:
    """Save the preference and create or remove the autostart file."""
    current = load_preferences(config_root)
    save_preferences(replace(current, start_at_login=enabled), config_root)
    try:
        if enabled:
            enable_autostart(autostart)
            ensure_application_desktop(applications)
        else:
            disable_autostart(autostart)
    except OSError as error:
        return ActionOutcome(
            ok=False,
            message="Could not update login startup.",
            detail=str(error),
        )
    return ActionOutcome(ok=True)


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
