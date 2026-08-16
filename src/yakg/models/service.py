"""State of the Kerio system service."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ServiceBackendKind(Enum):
    """Which init system YAKG is talking to."""

    SYSTEMD = "systemd"
    SYSVINIT = "sysvinit"
    NONE = "none"

    @property
    def label(self) -> str:
        return {
            ServiceBackendKind.SYSTEMD: "systemd",
            ServiceBackendKind.SYSVINIT: "SysV init",
            ServiceBackendKind.NONE: "No supported init system",
        }[self]


@dataclass(frozen=True)
class ServiceState:
    """A snapshot of the Kerio service.

    ``active`` and ``enabled_at_boot`` are tri-state. ``None`` means YAKG could
    not determine the answer, which is different from a confident "no" and must
    not be displayed as one. A unit that is mid-transition, or a probe that was
    denied or timed out, produces ``None``.
    """

    exists: bool
    backend: ServiceBackendKind = ServiceBackendKind.NONE
    unit_name: str = ""
    active: bool | None = None
    enabled_at_boot: bool | None = None

    #: Verbatim output from the init system, for the technical details view.
    raw: str = ""

    #: Human-readable explanation when something could not be determined.
    detail: str = ""

    @property
    def running_label(self) -> str:
        if not self.exists:
            return "Not found"
        if self.active is None:
            return "Unknown"
        return "Running" if self.active else "Stopped"

    @property
    def boot_label(self) -> str:
        if not self.exists:
            return "Not found"
        if self.enabled_at_boot is None:
            return "Unknown"
        return "Enabled" if self.enabled_at_boot else "Disabled"
