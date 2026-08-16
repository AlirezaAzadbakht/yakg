"""Tests for the subprocess runner.

These are the only tests that execute real commands, and they use nothing but
coreutils. Everything else in the suite fakes the runner out.
"""

from __future__ import annotations

import subprocess

import pytest

from yakg.backend.process import (
    RETURNCODE_NOT_EXECUTED,
    RETURNCODE_TIMED_OUT,
    CommandResult,
    run,
)


def test_successful_command_captures_stdout() -> None:
    result = run(["echo", "hello"])

    assert result.ok
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"
    assert result.executed
    assert not result.timed_out


def test_non_zero_exit_is_reported_not_raised() -> None:
    result = run(["false"])

    assert not result.ok
    assert result.returncode != 0
    assert result.executed


def test_missing_executable_is_reported() -> None:
    result = run(["yakg-command-that-does-not-exist"])

    assert not result.ok
    assert not result.executed
    assert result.returncode == RETURNCODE_NOT_EXECUTED
    assert "not installed" in result.error


def test_timeout_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    # Faked rather than genuinely slept through, so the suite stays fast and
    # does not depend on being allowed to spawn a long-running process.
    def raise_timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd=["slow"], timeout=0.5)

    monkeypatch.setattr(subprocess, "run", raise_timeout)

    result = run(["slow"], timeout=0.5)

    assert not result.ok
    assert result.timed_out
    assert result.returncode == RETURNCODE_TIMED_OUT
    assert "did not finish" in result.error


def test_unexpected_os_error_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_oserror(*_args: object, **_kwargs: object) -> None:
        raise OSError("exec format error")

    monkeypatch.setattr(subprocess, "run", raise_oserror)

    result = run(["broken"])

    assert not result.ok
    assert not result.executed
    assert "exec format error" in result.error


def test_permission_error_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_permission(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(subprocess, "run", raise_permission)

    result = run(["restricted"])

    assert not result.ok
    assert not result.executed
    assert "Not permitted" in result.error


def test_input_is_written_to_stdin() -> None:
    # Secrets travel this way rather than through argv, which /proc exposes.
    result = run(["cat"], input_text="from stdin")

    assert result.ok
    assert result.stdout == "from stdin"


def test_arguments_are_never_interpreted_by_a_shell() -> None:
    # A shell would treat this as a command separator; argv execution must not.
    payload = "; touch /tmp/yakg-should-not-exist"
    result = run(["echo", payload])

    assert result.ok
    assert result.stdout.strip() == payload


@pytest.mark.parametrize("argv", [[], ()])
def test_empty_argv_is_a_programming_error(argv: list[str]) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        run(argv)


def test_non_string_argv_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="only strings"):
        run(["echo", 42])  # type: ignore[list-item]


def test_output_combines_streams() -> None:
    result = CommandResult(argv=("x",), returncode=0, stdout=" out \n", stderr=" err ")

    assert result.output() == "out\nerr"
