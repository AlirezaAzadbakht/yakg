"""Control of the Kerio system service.

The Kerio package ships both a systemd unit and a SysV init script, so YAKG
supports both. Which one is in charge is decided at runtime, never assumed:
systemd is used only when it is actually PID 1, because ``systemctl`` is present
and partially functional even inside containers where it cannot manage anything.

Milestone 1 implements detection and status only. The state-changing operations
are declared here but not implemented, because doing so safely requires the
privileged helper and polkit policy planned for Milestone 2. They raise rather
than pretending to work.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from yakg.backend.constants import (
    DAEMON_PROCESS_NAME,
    SYSTEMD_UNIT,
    SYSVINIT_SCRIPT,
)
from yakg.backend.process import CommandResult, run
from yakg.models.service import ServiceBackendKind, ServiceState
from yakg.utils.logging import get_logger

log = get_logger(__name__)

#: The directory systemd creates when it is running as PID 1. This is the check
#: sd_booted(3) documents, and it is why YAKG does not simply look for the
#: systemctl binary: a container can have the binary and no running systemd.
SYSTEMD_RUNTIME_MARKER = Path("/run/systemd/system")

#: SysV runlevel directories that a started service is linked into.
DEFAULT_RC_DIRS: tuple[Path, ...] = (
    Path("/etc/rc2.d"),
    Path("/etc/rc3.d"),
    Path("/etc/rc4.d"),
    Path("/etc/rc5.d"),
)

#: systemctl emits this when it is installed but systemd is not running.
_NOT_BOOTED_MARKER = "has not been booted with systemd"

_ACTIVE_STATES = {"active", "reloading"}
_INACTIVE_STATES = {"inactive", "failed", "deactivating"}

_ENABLED_STATES = {"enabled", "enabled-runtime", "alias"}
_DISABLED_STATES = {"disabled", "masked", "masked-runtime"}


def systemd_is_running(marker: Path = SYSTEMD_RUNTIME_MARKER) -> bool:
    """True when systemd is the init system of the running host."""
    return marker.is_dir()


def parse_is_active(stdout: str) -> bool | None:
    """Interpret ``systemctl is-active`` output.

    ``None`` means indeterminate. ``activating`` lands there deliberately: the
    service is mid-transition and calling it either running or stopped would be
    a guess.
    """
    state = stdout.strip()
    if state in _ACTIVE_STATES:
        return True
    if state in _INACTIVE_STATES:
        return False
    return None


def parse_is_enabled(stdout: str) -> bool | None:
    """Interpret ``systemctl is-enabled`` output.

    ``static``, ``indirect`` and ``generated`` units have no enablement to
    report, so they yield ``None`` rather than a misleading boolean.
    """
    state = stdout.strip()
    if state in _ENABLED_STATES:
        return True
    if state in _DISABLED_STATES:
        return False
    return None


def parse_unit_exists(stdout: str, unit: str) -> bool:
    """Detect ``unit`` in ``systemctl list-unit-files`` output.

    The command exits successfully whether or not anything matched, so the
    output has to be inspected rather than the exit status.
    """
    for line in stdout.splitlines():
        fields = line.split()
        if fields and fields[0] == unit:
            return True
    return False


def parse_pgrep(result: CommandResult) -> bool | None:
    """Interpret ``pgrep``: 0 means matched, 1 means no match, anything else is
    an error and therefore unknown."""
    if not result.executed or result.timed_out:
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


class ServiceBackend:
    """Common interface over an init system.

    Subclasses implement status reporting. The control operations are shared
    here so that every backend reports the same, honest "not yet implemented"
    for Milestone 2 work.
    """

    kind: ServiceBackendKind = ServiceBackendKind.NONE

    def __init__(self, unit_name: str = SYSTEMD_UNIT) -> None:
        self.unit_name = unit_name

    def exists(self) -> bool:
        raise NotImplementedError

    def get_status(self) -> ServiceState:
        raise NotImplementedError

    # Milestone 2. These need a privileged helper invoked through polkit; until
    # that exists, failing loudly is better than a button that quietly does
    # nothing.
    def _unimplemented(self, operation: str) -> NotImplementedError:
        return NotImplementedError(
            f"{operation} requires the privileged helper planned for Milestone 2."
        )

    def start(self) -> None:
        raise self._unimplemented("Starting the service")

    def stop(self) -> None:
        raise self._unimplemented("Stopping the service")

    def restart(self) -> None:
        raise self._unimplemented("Restarting the service")

    def enable(self) -> None:
        raise self._unimplemented("Enabling the service at boot")

    def disable(self) -> None:
        raise self._unimplemented("Disabling the service at boot")


class SystemdBackend(ServiceBackend):
    """Status via ``systemctl``."""

    kind = ServiceBackendKind.SYSTEMD

    def exists(self) -> bool:
        result = run(
            ["systemctl", "list-unit-files", self.unit_name, "--no-legend", "--no-pager"],
        )
        return result.ok and parse_unit_exists(result.stdout, self.unit_name)

    def get_status(self) -> ServiceState:
        listing = run(
            ["systemctl", "list-unit-files", self.unit_name, "--no-legend", "--no-pager"],
        )
        if not listing.ok:
            return ServiceState(
                exists=False,
                backend=self.kind,
                unit_name=self.unit_name,
                raw=listing.output(),
                detail=listing.error or "Could not query systemd for the unit.",
            )

        if not parse_unit_exists(listing.stdout, self.unit_name):
            return ServiceState(
                exists=False,
                backend=self.kind,
                unit_name=self.unit_name,
                raw=listing.output(),
                detail=f"systemd does not know a unit named {self.unit_name}.",
            )

        is_active = run(["systemctl", "is-active", self.unit_name])
        is_enabled = run(["systemctl", "is-enabled", self.unit_name])

        # is-active and is-enabled exit non-zero for the perfectly ordinary
        # answers "inactive" and "disabled", so the output is what matters.
        active = parse_is_active(is_active.stdout)
        enabled = parse_is_enabled(is_enabled.stdout)

        detail = ""
        if active is None:
            detail = _explain_indeterminate(is_active, "running state")
        elif enabled is None:
            detail = _explain_indeterminate(is_enabled, "start-at-boot state")

        raw = "\n".join(
            part
            for part in (
                f"$ {listing.command}\n{listing.output()}",
                f"$ {is_active.command}\n{is_active.output()}",
                f"$ {is_enabled.command}\n{is_enabled.output()}",
            )
            if part
        )

        return ServiceState(
            exists=True,
            backend=self.kind,
            unit_name=self.unit_name,
            active=active,
            enabled_at_boot=enabled,
            raw=raw,
            detail=detail,
        )


class SysVInitBackend(ServiceBackend):
    """Status from the init script, the process table and the runlevel links.

    Liveness is checked with ``pgrep`` on the daemon name, which is exactly what
    the vendor's own init script does in its ``status()`` function.
    """

    kind = ServiceBackendKind.SYSVINIT

    def __init__(
        self,
        script_path: Path = SYSVINIT_SCRIPT,
        *,
        rc_dirs: Sequence[Path] = DEFAULT_RC_DIRS,
        process_name: str = DAEMON_PROCESS_NAME,
    ) -> None:
        super().__init__(unit_name=script_path.name)
        self.script_path = script_path
        self.rc_dirs = tuple(rc_dirs)
        self.process_name = process_name

    def exists(self) -> bool:
        return self.script_path.is_file()

    def get_status(self) -> ServiceState:
        if not self.exists():
            return ServiceState(
                exists=False,
                backend=self.kind,
                unit_name=self.unit_name,
                detail=f"No init script at {self.script_path}.",
            )

        pgrep = run(["pgrep", "-x", self.process_name])
        active = parse_pgrep(pgrep)
        enabled = self._enabled_at_boot()

        detail = ""
        if active is None:
            detail = _explain_indeterminate(pgrep, "running state")
        elif enabled is None:
            detail = "No runlevel directories were found, so the boot setting is unknown."

        return ServiceState(
            exists=True,
            backend=self.kind,
            unit_name=self.unit_name,
            active=active,
            enabled_at_boot=enabled,
            raw=f"$ {pgrep.command}\nexit {pgrep.returncode}\n{pgrep.output()}".strip(),
            detail=detail,
        )

    def _enabled_at_boot(self) -> bool | None:
        """Look for start links such as ``/etc/rc3.d/S27kerio-kvc``."""
        seen_any_dir = False
        for directory in self.rc_dirs:
            if not directory.is_dir():
                continue
            seen_any_dir = True
            if any(directory.glob(f"S*{self.script_path.name}")):
                return True
        return False if seen_any_dir else None


class NullBackend(ServiceBackend):
    """Used when no supported init system is available."""

    kind = ServiceBackendKind.NONE

    def exists(self) -> bool:
        return False

    def get_status(self) -> ServiceState:
        return ServiceState(
            exists=False,
            backend=self.kind,
            detail=(
                "Neither systemd nor a SysV init script for the Kerio service "
                "was found, so its state cannot be determined."
            ),
        )


def _explain_indeterminate(result: CommandResult, what: str) -> str:
    """Turn a failed probe into something a human can act on."""
    if result.timed_out:
        return f"Timed out while determining the {what}."
    if not result.executed:
        return result.error
    output = result.output()
    if _NOT_BOOTED_MARKER in output:
        return "systemd is installed but is not running as the init system."
    if output:
        return f"Could not determine the {what}: {output}"
    return f"Could not determine the {what}."


def select_backend(
    *,
    systemd_available: bool | None = None,
    sysv_script: Path = SYSVINIT_SCRIPT,
    unit_name: str = SYSTEMD_UNIT,
) -> ServiceBackend:
    """Choose the init system to talk to.

    systemd wins when it is genuinely running. Otherwise YAKG falls back to the
    init script the Kerio package also ships, which keeps the application usable
    on hosts, containers and WSL images that do not boot with systemd.
    """
    if systemd_available is None:
        systemd_available = systemd_is_running()

    if systemd_available:
        return SystemdBackend(unit_name=unit_name)
    if sysv_script.is_file():
        return SysVInitBackend(sysv_script)
    return NullBackend()


class ServiceManager:
    """Front door to service state, with the init system already resolved."""

    def __init__(self, backend: ServiceBackend | None = None) -> None:
        self.backend = backend if backend is not None else select_backend()
        log.info("Service backend: %s", self.backend.kind.value)

    @property
    def kind(self) -> ServiceBackendKind:
        return self.backend.kind

    def detect_service(self) -> bool:
        """Whether the Kerio service is known to this system at all."""
        return self.backend.exists()

    def get_service_status(self) -> ServiceState:
        state = self.backend.get_status()
        log.info(
            "Service status: exists=%s active=%s enabled=%s via %s",
            state.exists,
            state.active,
            state.enabled_at_boot,
            state.backend.value,
        )
        return state

    def start_service(self) -> None:
        self.backend.start()

    def stop_service(self) -> None:
        self.backend.stop()

    def restart_service(self) -> None:
        self.backend.restart()

    def enable_service(self) -> None:
        self.backend.enable()

    def disable_service(self) -> None:
        self.backend.disable()
