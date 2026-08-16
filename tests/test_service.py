"""Tests for service detection and status.

The systemctl output used here is the real thing, captured from the reference
machine described in docs/kerio-facts.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import InstallRunner, result
from yakg.backend.service import (
    NullBackend,
    ServiceManager,
    SystemdBackend,
    SysVInitBackend,
    parse_is_active,
    parse_is_enabled,
    parse_pgrep,
    parse_unit_exists,
    select_backend,
    systemd_is_running,
)
from yakg.models.service import ServiceBackendKind

MODULE = "yakg.backend.service"

# From: systemctl list-unit-files kerio-kvc.service --no-legend --no-pager
UNIT_LISTING = "kerio-kvc.service                              disabled enabled\n"


class TestParseIsActive:
    @pytest.mark.parametrize("output", ["active", "active\n", "reloading"])
    def test_running_states(self, output: str) -> None:
        assert parse_is_active(output) is True

    @pytest.mark.parametrize("output", ["inactive", "failed", "deactivating\n"])
    def test_stopped_states(self, output: str) -> None:
        assert parse_is_active(output) is False

    def test_transitional_state_is_not_a_guess(self) -> None:
        # Mid-transition is neither running nor stopped, and saying either
        # would be misleading.
        assert parse_is_active("activating") is None

    @pytest.mark.parametrize("output", ["", "unknown", "something-new"])
    def test_unrecognised_output_is_indeterminate(self, output: str) -> None:
        assert parse_is_active(output) is None


class TestParseIsEnabled:
    @pytest.mark.parametrize("output", ["enabled", "enabled-runtime", "alias\n"])
    def test_enabled_states(self, output: str) -> None:
        assert parse_is_enabled(output) is True

    @pytest.mark.parametrize("output", ["disabled", "masked", "masked-runtime"])
    def test_disabled_states(self, output: str) -> None:
        assert parse_is_enabled(output) is False

    @pytest.mark.parametrize("output", ["static", "indirect", "generated", "transient", ""])
    def test_states_with_no_enablement_are_indeterminate(self, output: str) -> None:
        assert parse_is_enabled(output) is None


class TestParseUnitExists:
    def test_finds_the_unit_in_real_output(self) -> None:
        assert parse_unit_exists(UNIT_LISTING, "kerio-kvc.service") is True

    def test_empty_output_means_no_unit(self) -> None:
        # list-unit-files exits 0 even when nothing matched, so the output is
        # the only signal.
        assert parse_unit_exists("", "kerio-kvc.service") is False

    def test_does_not_match_a_different_unit(self) -> None:
        assert parse_unit_exists("other.service enabled enabled\n", "kerio-kvc.service") is False

    def test_does_not_match_on_a_prefix(self) -> None:
        listing = "kerio-kvc-helper.service enabled enabled\n"

        assert parse_unit_exists(listing, "kerio-kvc.service") is False


class TestParsePgrep:
    def test_match_means_running(self) -> None:
        assert parse_pgrep(result(returncode=0, stdout="1234\n")) is True

    def test_no_match_means_stopped(self) -> None:
        # pgrep uses exit code 1 for "nothing matched", which is not an error.
        assert parse_pgrep(result(returncode=1)) is False

    def test_error_is_indeterminate(self) -> None:
        assert parse_pgrep(result(returncode=2, stderr="syntax error")) is None

    def test_missing_pgrep_is_indeterminate(self) -> None:
        assert parse_pgrep(result(returncode=-1, executed=False, error="missing")) is None

    def test_timeout_is_indeterminate(self) -> None:
        assert parse_pgrep(result(returncode=-2, timed_out=True)) is None


class TestSystemdBackend:
    def test_reports_a_stopped_disabled_service(self, fake_run: InstallRunner) -> None:
        # The state of the reference machine: installed but not enabled.
        fake_run(
            MODULE,
            {
                "list-unit-files": result(stdout=UNIT_LISTING),
                "is-active": result(returncode=3, stdout="inactive\n"),
                "is-enabled": result(returncode=1, stdout="disabled\n"),
            },
        )

        state = SystemdBackend().get_status()

        assert state.exists
        assert state.active is False
        assert state.enabled_at_boot is False
        assert state.backend is ServiceBackendKind.SYSTEMD
        assert state.running_label == "Stopped"
        assert state.boot_label == "Disabled"

    def test_reports_a_running_enabled_service(self, fake_run: InstallRunner) -> None:
        fake_run(
            MODULE,
            {
                "list-unit-files": result(stdout=UNIT_LISTING),
                "is-active": result(stdout="active\n"),
                "is-enabled": result(stdout="enabled\n"),
            },
        )

        state = SystemdBackend().get_status()

        assert state.active is True
        assert state.enabled_at_boot is True
        assert state.running_label == "Running"

    def test_non_zero_exit_from_is_active_is_not_treated_as_failure(
        self, fake_run: InstallRunner
    ) -> None:
        # systemctl is-active exits 3 for the perfectly ordinary answer
        # "inactive"; the output is what matters, not the exit status.
        fake_run(
            MODULE,
            {
                "list-unit-files": result(stdout=UNIT_LISTING),
                "is-active": result(returncode=3, stdout="inactive\n"),
                "is-enabled": result(returncode=1, stdout="disabled\n"),
            },
        )

        state = SystemdBackend().get_status()

        assert state.exists
        assert state.detail == ""

    def test_missing_unit_is_reported(self, fake_run: InstallRunner) -> None:
        fake_run(MODULE, {"list-unit-files": result(stdout="")})

        state = SystemdBackend().get_status()

        assert not state.exists
        assert "does not know a unit" in state.detail
        assert state.running_label == "Not found"

    def test_systemd_present_but_not_booted_is_explained(self, fake_run: InstallRunner) -> None:
        # Seen in containers and WSL: systemctl exists and can read unit files,
        # but cannot report runtime state.
        not_booted = (
            "System has not been booted with systemd as init system (PID 1). Can't operate."
        )
        fake_run(
            MODULE,
            {
                "list-unit-files": result(stdout=UNIT_LISTING),
                "is-active": result(returncode=1, stderr=not_booted),
                "is-enabled": result(stdout="disabled\n"),
            },
        )

        state = SystemdBackend().get_status()

        assert state.exists
        assert state.active is None
        assert state.running_label == "Unknown"
        assert "not running as the init system" in state.detail

    def test_missing_systemctl_is_reported(self, fake_run: InstallRunner) -> None:
        fake_run(
            MODULE,
            {"list-unit-files": result(returncode=-1, executed=False, error="systemctl missing")},
        )

        state = SystemdBackend().get_status()

        assert not state.exists
        assert "systemctl missing" in state.detail

    def test_timeout_is_reported_without_claiming_a_state(self, fake_run: InstallRunner) -> None:
        fake_run(
            MODULE,
            {
                "list-unit-files": result(stdout=UNIT_LISTING),
                "is-active": result(returncode=-2, timed_out=True),
                "is-enabled": result(stdout="disabled\n"),
            },
        )

        state = SystemdBackend().get_status()

        assert state.active is None
        assert "Timed out" in state.detail


class TestSysVInitBackend:
    @pytest.fixture
    def script(self, tmp_path: Path) -> Path:
        path = tmp_path / "init.d" / "kerio-kvc"
        path.parent.mkdir(parents=True)
        path.touch()
        return path

    def test_running_daemon_is_detected(self, fake_run: InstallRunner, script: Path) -> None:
        fake_run(MODULE, {"pgrep": result(returncode=0, stdout="4321\n")})

        state = SysVInitBackend(script, rc_dirs=[]).get_status()

        assert state.exists
        assert state.active is True
        assert state.backend is ServiceBackendKind.SYSVINIT
        assert state.unit_name == "kerio-kvc"

    def test_stopped_daemon_is_detected(self, fake_run: InstallRunner, script: Path) -> None:
        fake_run(MODULE, {"pgrep": result(returncode=1)})

        state = SysVInitBackend(script, rc_dirs=[]).get_status()

        assert state.active is False

    def test_missing_script_means_no_service(self, fake_run: InstallRunner, tmp_path: Path) -> None:
        fake_run(MODULE, {"pgrep": result(returncode=1)})

        state = SysVInitBackend(tmp_path / "absent").get_status()

        assert not state.exists
        assert "No init script" in state.detail

    def test_runlevel_link_means_enabled_at_boot(
        self, fake_run: InstallRunner, script: Path, tmp_path: Path
    ) -> None:
        # update-rc.d creates links such as /etc/rc3.d/S27kerio-kvc.
        rc3 = tmp_path / "rc3.d"
        rc3.mkdir()
        (rc3 / "S27kerio-kvc").touch()
        fake_run(MODULE, {"pgrep": result(returncode=1)})

        state = SysVInitBackend(script, rc_dirs=[rc3]).get_status()

        assert state.enabled_at_boot is True

    def test_only_a_stop_link_means_not_enabled(
        self, fake_run: InstallRunner, script: Path, tmp_path: Path
    ) -> None:
        rc3 = tmp_path / "rc3.d"
        rc3.mkdir()
        (rc3 / "K13kerio-kvc").touch()
        fake_run(MODULE, {"pgrep": result(returncode=1)})

        state = SysVInitBackend(script, rc_dirs=[rc3]).get_status()

        assert state.enabled_at_boot is False

    def test_absent_runlevel_directories_are_indeterminate(
        self, fake_run: InstallRunner, script: Path, tmp_path: Path
    ) -> None:
        fake_run(MODULE, {"pgrep": result(returncode=1)})

        state = SysVInitBackend(script, rc_dirs=[tmp_path / "absent"]).get_status()

        assert state.enabled_at_boot is None
        assert state.boot_label == "Unknown"


class TestSelectBackend:
    def test_prefers_systemd_when_it_is_running(self) -> None:
        assert isinstance(select_backend(systemd_available=True), SystemdBackend)

    def test_falls_back_to_sysvinit(self, tmp_path: Path) -> None:
        script = tmp_path / "kerio-kvc"
        script.touch()

        backend = select_backend(systemd_available=False, sysv_script=script)

        assert isinstance(backend, SysVInitBackend)

    def test_no_init_system_yields_the_null_backend(self, tmp_path: Path) -> None:
        backend = select_backend(systemd_available=False, sysv_script=tmp_path / "absent")

        assert isinstance(backend, NullBackend)
        assert backend.get_status().exists is False

    def test_systemd_detection_uses_the_runtime_marker(self, tmp_path: Path) -> None:
        # sd_booted(3) checks for this directory. Merely having the systemctl
        # binary does not mean systemd is running.
        assert systemd_is_running(tmp_path / "absent") is False
        assert systemd_is_running(tmp_path) is True


class TestServiceManager:
    def test_delegates_status_to_its_backend(self, fake_run: InstallRunner) -> None:
        fake_run(
            MODULE,
            {
                "list-unit-files": result(stdout=UNIT_LISTING),
                "is-active": result(stdout="active\n"),
                "is-enabled": result(stdout="enabled\n"),
            },
        )

        manager = ServiceManager(SystemdBackend())

        assert manager.get_service_status().active is True
        assert manager.detect_service() is True
        assert manager.kind is ServiceBackendKind.SYSTEMD

    @pytest.mark.parametrize(
        "operation",
        ["start_service", "stop_service", "restart_service", "enable_service", "disable_service"],
    )
    def test_control_operations_refuse_rather_than_pretend(self, operation: str) -> None:
        # Milestone 2 work. A button that silently does nothing would be worse
        # than one that reports it is not implemented.
        manager = ServiceManager(NullBackend())

        with pytest.raises(NotImplementedError, match="Milestone 2"):
            getattr(manager, operation)()
