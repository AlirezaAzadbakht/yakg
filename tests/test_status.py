"""Tests for snapshot gathering and connection-state inference."""

from __future__ import annotations

from pathlib import Path

from conftest import InstallRunner, result
from yakg.backend.service import ServiceManager, SystemdBackend
from yakg.backend.status import gather_status, infer_connection_state
from yakg.main import format_report
from yakg.models.connection import ConnectionState
from yakg.models.installation import Installation
from yakg.models.service import ServiceBackendKind, ServiceState

UNIT_LISTING = "kerio-kvc.service                              disabled enabled\n"

INSTALLED = Installation(installed=True, version="9.6.1.9306.0.patch1-1")
SERVICE_FOUND = ServiceState(exists=True, backend=ServiceBackendKind.SYSTEMD)


class TestInferConnectionState:
    def test_stopped_service_means_definitely_disconnected(self) -> None:
        # The daemon connects by itself when it starts, so no daemon means no
        # tunnel. That much can be stated with confidence.
        state = infer_connection_state(
            INSTALLED,
            ServiceState(exists=True, backend=ServiceBackendKind.SYSTEMD, active=False),
        )

        assert state is ConnectionState.DISCONNECTED

    def test_running_service_is_not_assumed_to_be_connected(self) -> None:
        # A running daemon may still be retrying or failing to authenticate.
        # Confirming a live tunnel needs Milestone 3 facts.
        state = infer_connection_state(
            INSTALLED,
            ServiceState(exists=True, backend=ServiceBackendKind.SYSTEMD, active=True),
        )

        assert state is ConnectionState.UNKNOWN

    def test_indeterminate_service_state_stays_unknown(self) -> None:
        state = infer_connection_state(INSTALLED, SERVICE_FOUND)

        assert state is ConnectionState.UNKNOWN

    def test_missing_client_is_unknown_not_disconnected(self) -> None:
        state = infer_connection_state(Installation(installed=False), ServiceState(exists=False))

        assert state is ConnectionState.UNKNOWN


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
        assert snapshot.connection is ConnectionState.DISCONNECTED
        assert snapshot.usable


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

    def test_report_is_readable_when_nothing_is_installed(self) -> None:
        from yakg.models.snapshot import SystemSnapshot

        report = format_report(
            SystemSnapshot(
                installation=Installation(installed=False, detail="Nothing found."),
                service=ServiceState(exists=False),
            )
        )

        assert "Installed:      no" in report
        assert "Unknown" in report
        assert "Nothing found." in report
