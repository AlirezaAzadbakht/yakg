"""Shared test helpers.

The suite never touches the real system: :func:`fake_run` replaces
``yakg.backend.process.run`` wherever a module imported it, so no test can
accidentally invoke ``systemctl`` or ``dpkg-query`` on the machine running it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from yakg.backend.process import CommandResult


def result(
    argv: Sequence[str] = ("fake",),
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    executed: bool = True,
    error: str = "",
) -> CommandResult:
    """Build a :class:`CommandResult` with sensible defaults."""
    return CommandResult(
        argv=tuple(argv),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        executed=executed,
        error=error,
    )


@dataclass
class FakeRunner:
    """A stand-in for :func:`yakg.backend.process.run`.

    Responses are matched on a substring of the command line, which keeps the
    tests readable: a test says what ``systemctl is-active`` should answer,
    without restating the whole argument vector.
    """

    responses: dict[str, CommandResult] = field(default_factory=dict)
    calls: list[tuple[str, ...]] = field(default_factory=list)
    kwargs_list: list[dict[str, object]] = field(default_factory=list)
    default: CommandResult | None = None

    def __call__(self, argv: Sequence[str], **kwargs: object) -> CommandResult:
        command = tuple(argv)
        self.calls.append(command)
        self.kwargs_list.append(dict(kwargs))
        line = " ".join(command)

        for pattern, response in self.responses.items():
            if pattern in line:
                return CommandResult(
                    argv=command,
                    returncode=response.returncode,
                    stdout=response.stdout,
                    stderr=response.stderr,
                    timed_out=response.timed_out,
                    executed=response.executed,
                    error=response.error,
                )

        if self.default is not None:
            return self.default
        return result(command, returncode=1, executed=False, error="unexpected command")

    def ran(self, fragment: str) -> bool:
        """True when some executed command line contained ``fragment``."""
        return any(fragment in " ".join(call) for call in self.calls)


#: Signature of the factory returned by the ``fake_run`` fixture.
InstallRunner = Callable[..., FakeRunner]


@pytest.fixture(autouse=True)
def isolate_xdg_config(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Keep YAKG files out of the real home, autostart, and applications dirs."""
    root = tmp_path_factory.mktemp("xdg-config")
    data = tmp_path_factory.mktemp("xdg-data")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    return root


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> InstallRunner:
    """Return a factory that installs a :class:`FakeRunner` into a module.

    ``module`` is the module whose imported ``run`` should be replaced, for
    example ``"yakg.backend.service"``.
    """

    def install(
        module: str,
        responses: dict[str, CommandResult] | None = None,
        *,
        default: CommandResult | None = None,
    ) -> FakeRunner:
        runner = FakeRunner(responses=dict(responses or {}), default=default)
        monkeypatch.setattr(f"{module}.run", runner)
        return runner

    return install
