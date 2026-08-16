"""Detection of the installed Kerio Control VPN Client.

Detection is read-only. It runs ``dpkg-query`` and inspects paths; it never
opens ``/etc/kerio-kvc.conf``, which holds the account password and would need
root anyway. Presence of the configuration is established with a stat.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from pathlib import Path

from yakg.backend.constants import (
    CONFIG_CANDIDATES,
    EXECUTABLE_CANDIDATES,
    EXECUTABLE_NAME,
    LOG_DIR_CANDIDATES,
    PACKAGE_NAME,
)
from yakg.backend.process import run
from yakg.models.installation import Installation
from yakg.utils.logging import get_logger

log = get_logger(__name__)

#: dpkg reports the package state separately from the version, because a purged
#: package can still have a version recorded. Only this state means installed.
_DPKG_STATE_INSTALLED = "installed"


def parse_dpkg_query(stdout: str) -> str | None:
    """Extract the version from ``dpkg-query -W -f=${db:Status-Status}\\t${Version}``.

    Returns ``None`` unless the package is genuinely installed. A package that
    was removed without being purged still has a version on record, and
    reporting that as an installed client would be wrong.
    """
    line = stdout.strip()
    if not line:
        return None

    state, separator, version = line.partition("\t")
    if not separator:
        return None
    if state.strip() != _DPKG_STATE_INSTALLED:
        return None

    return version.strip() or None


def query_package_version(package: str = PACKAGE_NAME) -> str | None:
    """Return the installed version of ``package``, or ``None``.

    ``None`` also covers systems with no dpkg at all, which is the expected
    answer on a non-Debian distribution rather than an error.
    """
    result = run(
        ["dpkg-query", "-W", "-f=${db:Status-Status}\t${Version}", package],
        timeout=5.0,
    )
    if not result.ok:
        return None
    return parse_dpkg_query(result.stdout)


def find_executable(
    candidates: Sequence[Path] = EXECUTABLE_CANDIDATES,
    *,
    path_lookup: Callable[[str], str | None] = shutil.which,
) -> Path | None:
    """Return the Kerio daemon binary, or ``None`` if it is not present.

    Known locations are checked first, then ``PATH``. The daemon normally lives
    in ``/usr/sbin``, which is not on a desktop user's ``PATH``, so the explicit
    candidates matter more than the lookup does.
    """
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    found = path_lookup(EXECUTABLE_NAME)
    return Path(found) if found else None


def _first_existing(candidates: Sequence[Path], *, directory: bool = False) -> Path | None:
    for candidate in candidates:
        if candidate.is_dir() if directory else candidate.exists():
            return candidate
    return None


def detect_client(
    *,
    executable_candidates: Sequence[Path] = EXECUTABLE_CANDIDATES,
    config_candidates: Sequence[Path] = CONFIG_CANDIDATES,
    log_dir_candidates: Sequence[Path] = LOG_DIR_CANDIDATES,
    path_lookup: Callable[[str], str | None] = shutil.which,
) -> Installation:
    """Determine whether the Kerio Control VPN Client is installed."""
    version = query_package_version()
    executable = find_executable(executable_candidates, path_lookup=path_lookup)
    config_path = _first_existing(config_candidates)
    log_dir = _first_existing(log_dir_candidates, directory=True)

    if executable is not None:
        detected_via = "dpkg and executable" if version else "executable search"
    elif version is not None:
        # The package is registered but its binary is gone: a broken install,
        # which is worth telling the user about rather than silently accepting.
        detected_via = "dpkg"
    else:
        detected_via = ""

    installed = executable is not None or version is not None

    detail = ""
    if not installed:
        detail = (
            f"No {PACKAGE_NAME} package is registered with dpkg and no "
            f"{EXECUTABLE_NAME} executable was found."
        )
    elif executable is None:
        detail = (
            f"dpkg reports {PACKAGE_NAME} {version} as installed, but the "
            f"{EXECUTABLE_NAME} executable is missing. The package may be damaged."
        )

    installation = Installation(
        installed=installed,
        version=version,
        executable=executable,
        config_path=config_path,
        config_present=config_path is not None,
        log_dir=log_dir,
        detected_via=detected_via,
        detail=detail,
    )

    log.info(
        "Kerio client detection: installed=%s version=%s executable=%s configured=%s",
        installation.installed,
        installation.version_display,
        installation.executable,
        installation.config_present,
    )
    return installation
