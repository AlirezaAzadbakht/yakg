"""Tests for Kerio client detection."""

from __future__ import annotations

from pathlib import Path

from conftest import InstallRunner, result
from yakg.backend import constants
from yakg.backend.detect import (
    detect_client,
    find_executable,
    parse_dpkg_query,
    query_package_version,
)

MODULE = "yakg.backend.detect"

# Verbatim from the reference machine, via
# dpkg-query -W -f='${db:Status-Status}\t${Version}' kerio-control-vpnclient
DPKG_INSTALLED = "installed\t9.6.1.9306.0.patch1-1"


class TestParseDpkgQuery:
    def test_installed_package_yields_its_version(self) -> None:
        assert parse_dpkg_query(DPKG_INSTALLED) == "9.6.1.9306.0.patch1-1"

    def test_trailing_newline_is_tolerated(self) -> None:
        assert parse_dpkg_query(f"{DPKG_INSTALLED}\n") == "9.6.1.9306.0.patch1-1"

    def test_removed_but_not_purged_package_is_not_installed(self) -> None:
        # dpkg still records a version for a package in config-files state.
        # Reporting that as an installed client would be wrong.
        assert parse_dpkg_query("config-files\t9.6.1.9306.0.patch1-1") is None

    def test_not_installed_state_is_rejected(self) -> None:
        assert parse_dpkg_query("not-installed\t") is None

    def test_empty_output_yields_nothing(self) -> None:
        assert parse_dpkg_query("") is None

    def test_output_without_the_expected_separator_yields_nothing(self) -> None:
        assert parse_dpkg_query("9.6.1.9306.0.patch1-1") is None


class TestQueryPackageVersion:
    def test_reads_the_version_from_dpkg(self, fake_run: InstallRunner) -> None:
        fake_run(MODULE, {"dpkg-query": result(stdout=DPKG_INSTALLED)})

        assert query_package_version() == "9.6.1.9306.0.patch1-1"

    def test_unknown_package_yields_nothing(self, fake_run: InstallRunner) -> None:
        fake_run(
            MODULE,
            {"dpkg-query": result(returncode=1, stderr="no packages found matching kerio")},
        )

        assert query_package_version() is None

    def test_absent_dpkg_is_not_an_error(self, fake_run: InstallRunner) -> None:
        # Expected on a non-Debian distribution rather than a failure.
        fake_run(MODULE, {"dpkg-query": result(returncode=-1, executed=False, error="missing")})

        assert query_package_version() is None

    def test_timeout_yields_nothing(self, fake_run: InstallRunner) -> None:
        fake_run(MODULE, {"dpkg-query": result(returncode=-2, timed_out=True)})

        assert query_package_version() is None


class TestFindExecutable:
    def test_first_existing_candidate_wins(self, tmp_path: Path) -> None:
        first = tmp_path / "usr-sbin" / "kvpncsvc"
        second = tmp_path / "usr-local-sbin" / "kvpncsvc"
        for candidate in (first, second):
            candidate.parent.mkdir(parents=True)
            candidate.touch()

        assert find_executable([first, second]) == first

    def test_falls_back_to_a_later_candidate(self, tmp_path: Path) -> None:
        missing = tmp_path / "absent" / "kvpncsvc"
        present = tmp_path / "present" / "kvpncsvc"
        present.parent.mkdir(parents=True)
        present.touch()

        assert find_executable([missing, present]) == present

    def test_falls_back_to_path_lookup(self, tmp_path: Path) -> None:
        # /usr/sbin is not on a desktop user's PATH, so the lookup is a last
        # resort rather than the primary mechanism.
        found = tmp_path / "kvpncsvc"

        assert find_executable([], path_lookup=lambda _name: str(found)) == found

    def test_reports_nothing_when_absent_everywhere(self, tmp_path: Path) -> None:
        assert find_executable([tmp_path / "nope"], path_lookup=lambda _name: None) is None

    def test_a_directory_is_not_an_executable(self, tmp_path: Path) -> None:
        directory = tmp_path / "kvpncsvc"
        directory.mkdir()

        assert find_executable([directory], path_lookup=lambda _name: None) is None


class TestDetectClient:
    def test_fully_installed_and_configured_system(
        self, fake_run: InstallRunner, tmp_path: Path
    ) -> None:
        executable = tmp_path / "kvpncsvc"
        executable.touch()
        config = tmp_path / "kerio-kvc.conf"
        config.touch()
        logs = tmp_path / "logs"
        logs.mkdir()
        fake_run(MODULE, {"dpkg-query": result(stdout=DPKG_INSTALLED)})

        installation = detect_client(
            executable_candidates=[executable],
            config_candidates=[config],
            log_dir_candidates=[logs],
        )

        assert installation.installed
        assert installation.version == "9.6.1.9306.0.patch1-1"
        assert installation.executable == executable
        assert installation.config_present
        assert installation.config_path == config
        assert installation.log_dir == logs
        assert not installation.needs_configuration

    def test_installed_but_never_configured(self, fake_run: InstallRunner, tmp_path: Path) -> None:
        # The package does not ship /etc/kerio-kvc.conf; the wizard writes it.
        executable = tmp_path / "kvpncsvc"
        executable.touch()
        fake_run(MODULE, {"dpkg-query": result(stdout=DPKG_INSTALLED)})

        installation = detect_client(
            executable_candidates=[executable],
            config_candidates=[tmp_path / "absent.conf"],
            log_dir_candidates=[tmp_path / "absent"],
        )

        assert installation.installed
        assert not installation.config_present
        assert installation.needs_configuration

    def test_not_installed_at_all(self, fake_run: InstallRunner, tmp_path: Path) -> None:
        fake_run(MODULE, {"dpkg-query": result(returncode=1)})

        installation = detect_client(
            executable_candidates=[tmp_path / "absent"],
            config_candidates=[tmp_path / "absent.conf"],
            log_dir_candidates=[tmp_path / "absent"],
            path_lookup=lambda _name: None,
        )

        assert not installation.installed
        assert installation.version is None
        assert installation.version_display == "Unknown"
        assert "No kerio-control-vpnclient package" in installation.detail

    def test_executable_without_dpkg_still_counts_as_installed(
        self, fake_run: InstallRunner, tmp_path: Path
    ) -> None:
        # A manually installed or relocated client, or a non-Debian system.
        executable = tmp_path / "kvpncsvc"
        executable.touch()
        fake_run(MODULE, {"dpkg-query": result(returncode=1)})

        installation = detect_client(
            executable_candidates=[executable],
            config_candidates=[tmp_path / "absent.conf"],
            log_dir_candidates=[tmp_path / "absent"],
        )

        assert installation.installed
        assert installation.detected_via == "executable search"
        assert installation.version is None

    def test_registered_package_with_a_missing_binary_is_flagged(
        self, fake_run: InstallRunner, tmp_path: Path
    ) -> None:
        fake_run(MODULE, {"dpkg-query": result(stdout=DPKG_INSTALLED)})

        installation = detect_client(
            executable_candidates=[tmp_path / "absent"],
            config_candidates=[tmp_path / "absent.conf"],
            log_dir_candidates=[tmp_path / "absent"],
            path_lookup=lambda _name: None,
        )

        assert installation.installed
        assert installation.executable is None
        assert "may be damaged" in installation.detail

    def test_detection_never_opens_the_configuration_file(
        self, fake_run: InstallRunner, tmp_path: Path
    ) -> None:
        # The file holds the account password, so presence is established with a
        # stat and nothing more.
        config = tmp_path / "kerio-kvc.conf"
        config.write_text("<config><password>XOR:3a</password></config>")
        config.chmod(0o000)
        fake_run(MODULE, {"dpkg-query": result(stdout=DPKG_INSTALLED)})

        installation = detect_client(
            executable_candidates=[tmp_path / "absent"],
            config_candidates=[config],
            log_dir_candidates=[tmp_path / "absent"],
            path_lookup=lambda _name: None,
        )

        assert installation.config_present

    def test_uses_the_verified_default_candidates(self) -> None:
        # A guard against the verified paths drifting out of the constants.
        assert Path("/usr/sbin/kvpncsvc") in constants.EXECUTABLE_CANDIDATES
        assert Path("/etc/kerio-kvc.conf") in constants.CONFIG_CANDIDATES
        assert constants.SYSTEMD_UNIT == "kerio-kvc.service"
