"""What YAKG knows about the installed Kerio Control VPN Client."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Installation:
    """The result of looking for the Kerio client on this machine.

    ``config_present`` is deliberately separate from ``installed``: the package
    does not ship ``/etc/kerio-kvc.conf``, it is written by the configuration
    wizard, so a correctly installed client can still be unconfigured. YAKG
    reports that as a distinct, actionable state.
    """

    installed: bool
    version: str | None = None
    executable: Path | None = None
    config_path: Path | None = None
    config_present: bool = False
    log_dir: Path | None = None

    #: How the client was found, e.g. "dpkg" or "executable search".
    detected_via: str = ""

    #: Why detection failed, when it did. Shown only in the details view.
    detail: str = ""

    @property
    def version_display(self) -> str:
        """The version for display, never an empty string."""
        return self.version or "Unknown"

    @property
    def needs_configuration(self) -> bool:
        """True when the client is installed but has never been configured."""
        return self.installed and not self.config_present
