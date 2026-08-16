"""Verified facts about the Kerio Control VPN Client installation layout.

Every value here was confirmed against a real installation. The evidence, and
the version it was taken from, is recorded in ``docs/kerio-facts.md``. Do not
add a value to this module that has not been verified the same way.

These are *candidates* and defaults, not assumptions: detection always confirms
a path exists before reporting it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

#: Debian package name, from ``dpkg -l``.
PACKAGE_NAME: Final = "kerio-control-vpnclient"

#: The daemon binary. The package installs exactly one executable.
EXECUTABLE_NAME: Final = "kvpncsvc"

#: Searched in order. ``/usr/sbin`` is where the package puts it; the others
#: cover a locally installed or relocated client.
EXECUTABLE_CANDIDATES: Final[tuple[Path, ...]] = (
    Path("/usr/sbin/kvpncsvc"),
    Path("/usr/local/sbin/kvpncsvc"),
    Path("/sbin/kvpncsvc"),
)

#: Written by the package's configuration wizard, not shipped by the package.
#: Mode 0600, owned by root, and contains a recoverable password.
CONFIG_CANDIDATES: Final[tuple[Path, ...]] = (Path("/etc/kerio-kvc.conf"),)

#: World-readable, so YAKG can show logs without elevated privileges.
LOG_DIR_CANDIDATES: Final[tuple[Path, ...]] = (Path("/var/log/kerio-kvc"),)

#: Log file names inside the log directory, per the vendor README.
LOG_FILE_NAMES: Final[tuple[str, ...]] = ("init.log", "error.log", "debug.log")

#: The canonical systemd unit.
SYSTEMD_UNIT: Final = "kerio-kvc.service"

#: Aliases declared in the unit's ``[Install]`` section. Users may refer to the
#: service by these names, so error messages should recognise them.
SYSTEMD_UNIT_ALIASES: Final[tuple[str, ...]] = (
    "kerio-vpn",
    "kerio-control-vpn",
    "kerio-control-vpnclient",
)

#: SysV init script, shipped alongside the systemd unit.
SYSVINIT_SCRIPT: Final = Path("/etc/init.d/kerio-kvc")

#: Passed to the daemon as its working directory; holds the logs symlink.
RUNTIME_DIR: Final = Path("/var/lib/kerio-control-vpn")

#: Process name the vendor init script itself matches on with ``pgrep``.
DAEMON_PROCESS_NAME: Final = "kvpncsvc"

#: Default VPN port used when the user gives a bare hostname.
DEFAULT_PORT: Final = 4090
