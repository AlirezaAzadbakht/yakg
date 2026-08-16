"""Tests for the unprivileged pkexec wrapper around yakg-helper."""

from __future__ import annotations

from pathlib import Path

import pytest

from config_fixtures import POSTINST_XML
from conftest import InstallRunner, result
from yakg.backend.config_access import (
    MISSING_HELPER_MESSAGE,
    read_config,
    restore_config,
    write_config,
)
from yakg.backend.process import CONTROL_TIMEOUT_SECONDS

MODULE = "yakg.backend.config_access"
HELPER = "/usr/libexec/yakg-helper"


@pytest.fixture
def helper_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{MODULE}.helper_available", lambda _helper=None: True)


class TestReadConfig:
    def test_builds_pkexec_read_and_parses_stdout(
        self, fake_run: InstallRunner, helper_present: None
    ) -> None:
        runner = fake_run(MODULE, {f"{HELPER} read": result(stdout=POSTINST_XML)})

        loaded = read_config()

        assert loaded.outcome.ok
        assert loaded.config is not None
        assert loaded.config.password_xor.startswith("XOR:")
        assert loaded.config.password_plain == ""
        assert runner.calls[-1] == ("pkexec", HELPER, "read")
        assert POSTINST_XML not in " ".join(runner.calls[-1])

    def test_missing_file_is_not_a_failure(
        self, fake_run: InstallRunner, helper_present: None
    ) -> None:
        fake_run(MODULE, {f"{HELPER} read": result(returncode=3, stderr="missing")})

        loaded = read_config()

        assert loaded.missing
        assert loaded.outcome.ok
        assert loaded.config is None

    def test_dismissed_prompt_is_cancelled(
        self, fake_run: InstallRunner, helper_present: None
    ) -> None:
        fake_run(
            MODULE,
            {
                f"{HELPER} read": result(
                    returncode=126,
                    stderr="Error executing command as another user: Request dismissed",
                )
            },
        )

        loaded = read_config()

        assert loaded.outcome.cancelled
        assert loaded.config is None


class TestWriteConfig:
    def test_sends_xml_on_stdin_never_argv(
        self, fake_run: InstallRunner, helper_present: None
    ) -> None:
        runner = fake_run(MODULE, {f"{HELPER} write": result()})

        outcome = write_config(POSTINST_XML)

        assert outcome.ok
        assert runner.calls[-1] == ("pkexec", HELPER, "write")
        assert "<config>" not in " ".join(runner.calls[-1])
        assert runner.kwargs_list[-1]["input_text"] == POSTINST_XML
        assert runner.kwargs_list[-1]["timeout"] == CONTROL_TIMEOUT_SECONDS

    def test_access_denied_is_cancelled(
        self, fake_run: InstallRunner, helper_present: None
    ) -> None:
        fake_run(MODULE, {f"{HELPER} write": result(returncode=1, stderr="Access denied\n")})

        outcome = write_config(POSTINST_XML)

        assert outcome.cancelled
        assert outcome.message == ""


class TestRestoreConfig:
    def test_rejects_traversal_before_pkexec(self, fake_run: InstallRunner) -> None:
        runner = fake_run(MODULE, {})

        outcome = restore_config("../etc/passwd")

        assert not outcome.ok
        assert runner.calls == []
        assert "not allowed" in outcome.message

    def test_builds_pkexec_restore_with_a_basename(
        self, fake_run: InstallRunner, helper_present: None
    ) -> None:
        runner = fake_run(MODULE, {f"{HELPER} restore": result()})

        outcome = restore_config("kerio-kvc.conf.20260816T145430Z")

        assert outcome.ok
        assert runner.calls[-1] == (
            "pkexec",
            HELPER,
            "restore",
            "kerio-kvc.conf.20260816T145430Z",
        )


class TestMissingHelper:
    def test_read_explains_how_to_install(self, tmp_path: Path) -> None:
        loaded = read_config(helper=tmp_path / "absent")

        assert not loaded.outcome.ok
        assert loaded.outcome.message == MISSING_HELPER_MESSAGE

    def test_write_explains_how_to_install(self, tmp_path: Path) -> None:
        outcome = write_config(POSTINST_XML, helper=tmp_path / "absent")

        assert not outcome.ok
        assert outcome.message == MISSING_HELPER_MESSAGE
