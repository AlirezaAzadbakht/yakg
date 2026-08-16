"""Tests for snapshot gathering and connection-state inference."""

from __future__ import annotations

from pathlib import Path

from conftest import InstallRunner, result
from yakg.backend import status as status_module
from yakg.backend.profiles import ProfileStore, load_profile_state, save_profile, set_active_id
from yakg.backend.service import ServiceManager, SystemdBackend
from yakg.backend.status import gather_status, infer_connection_state
from yakg.main import format_report
from yakg.models.connection import ConnectionState
from yakg.models.installation import Installation
from yakg.models.profile import Profile
from yakg.models.service import ServiceBackendKind, ServiceState
from yakg.models.snapshot import SystemSnapshot

UNIT_LISTING = "kerio-kvc.service                              disabled enabled\n"

INSTALLED = Installation(installed=True, version="9.6.1.9306.0.patch1-1")
SERVICE_FOUND = ServiceState(exists=True, backend=ServiceBackendKind.SYSTEMD)


class TestInferConnectionState:
    def test_stopped_service_means_definitely_disconnected(self) -> None:
        info = infer_connection_state(
            INSTALLED,
            ServiceState(exists=True, backend=ServiceBackendKind.SYSTEMD, active=False),
        )

        assert info.state is ConnectionState.DISCONNECTED

    def test_running_service_without_fsm_is_connecting(self) -> None:
        info = infer_connection_state(
            INSTALLED,
            ServiceState(exists=True, backend=ServiceBackendKind.SYSTEMD, active=True),
        )

        assert info.state is ConnectionState.CONNECTING

    def test_indeterminate_service_state_stays_unknown(self) -> None:
        info = infer_connection_state(INSTALLED, SERVICE_FOUND)

        assert info.state is ConnectionState.UNKNOWN

    def test_missing_client_is_unknown_not_disconnected(self) -> None:
        info = infer_connection_state(Installation(installed=False), ServiceState(exists=False))

        assert info.state is ConnectionState.UNKNOWN


class TestGatherStatus:
    def test_combines_detection_and_service_state(
        self, fake_run: InstallRunner, tmp_path: Path
    ) -> None:
        executable = tmp_path / "kvpncsvc"
        executable.touch()

        fake_run(
            "yakg.backend.detect",
            {"dpkg-query": result(stdout="installed\t9.6.1.9306.0.patch1-1")},
        )
        fake_run(
            "yakg.backend.service",
            {
                "list-unit-files": result(stdout=UNIT_LISTING),
                "is-active": result(returncode=3, stdout="inactive\n"),
                "is-enabled": result(returncode=1, stdout="disabled\n"),
            },
        )

        snapshot = gather_status(ServiceManager(SystemdBackend()))

        assert snapshot.installation.installed
        assert snapshot.service.exists
        assert snapshot.service.active is False
        assert snapshot.connection.state is ConnectionState.DISCONNECTED
        assert snapshot.usable
        assert snapshot.profiles.profiles == ()

    def test_does_not_read_the_kerio_configuration_file(self) -> None:
        # Presence is a stat. Opening /etc/kerio-kvc.conf is the helper's job.
        text = Path(status_module.__file__).read_text(encoding="utf-8")
        assert "parse_config" not in text
        assert "config_access" not in text
        assert "kerio-kvc.conf" not in text

    def test_loads_profiles_from_an_injected_store(
        self, fake_run: InstallRunner, tmp_path: Path
    ) -> None:
        fake_run(
            "yakg.backend.detect",
            {"dpkg-query": result(stdout="installed\t9.6.1.9306.0.patch1-1")},
        )
        fake_run(
            "yakg.backend.service",
            {
                "list-unit-files": result(stdout=UNIT_LISTING),
                "is-active": result(returncode=3, stdout="inactive\n"),
                "is-enabled": result(returncode=1, stdout="disabled\n"),
            },
        )
        store = ProfileStore(tmp_path / "yakg")
        profile = save_profile(
            store,
            Profile(
                id="11111111-1111-1111-1111-111111111111",
                name="Work",
                server="vpn.example.com",
                port=4090,
                username="someone",
                fingerprint="AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99",
            ),
        )
        set_active_id(store, profile.id)

        snapshot = gather_status(ServiceManager(SystemdBackend()), store=store)

        assert snapshot.profiles.active_id == profile.id
        assert snapshot.profiles.profiles[0].name == "Work"


class TestFormatReport:
    def test_report_covers_the_essentials(self, fake_run: InstallRunner, tmp_path: Path) -> None:
        fake_run(
            "yakg.backend.detect",
            {"dpkg-query": result(stdout="installed\t9.6.1.9306.0.patch1-1")},
        )
        fake_run(
            "yakg.backend.service",
            {
                "list-unit-files": result(stdout=UNIT_LISTING),
                "is-active": result(returncode=3, stdout="inactive\n"),
                "is-enabled": result(returncode=1, stdout="disabled\n"),
            },
        )

        report = format_report(gather_status(ServiceManager(SystemdBackend())))

        assert "9.6.1.9306.0.patch1-1" in report
        assert "Stopped" in report
        assert "Disconnected" in report
        assert "Active:         none" in report

    def test_active_profile_line_never_includes_the_username(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "yakg")
        profile = save_profile(
            store,
            Profile(
                id="11111111-1111-1111-1111-111111111111",
                name="Work",
                server="vpn.example.com",
                port=4090,
                username="secret-user",
                fingerprint="AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99",
            ),
        )
        set_active_id(store, profile.id)
        snapshot = SystemSnapshot(
            installation=Installation(installed=False),
            service=ServiceState(exists=False),
            profiles=load_profile_state(store),
        )

        report = format_report(snapshot)

        assert "Work (vpn.example.com)" in report
        assert "secret-user" not in report

    def test_report_is_readable_when_nothing_is_installed(self) -> None:
        report = format_report(
            SystemSnapshot(
                installation=Installation(installed=False, detail="Nothing found."),
                service=ServiceState(exists=False),
            )
        )

        assert "Installed:      no" in report
        assert "Unknown" in report
        assert "Nothing found." in report
