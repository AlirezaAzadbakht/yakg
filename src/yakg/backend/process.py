"""The single place where YAKG executes an external command.

Two rules hold everywhere in this module, and they are the reason it exists at
all:

* commands are argument vectors, never strings handed to a shell, so no value
  YAKG handles can ever be interpreted as shell syntax;
* every call has a timeout, so a hung system command cannot wedge the caller.

Secrets are passed on stdin via ``input_text``. They must never appear in
``argv``, which is world-readable through ``/proc`` for as long as the process
lives.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from yakg.utils.logging import get_logger
from yakg.utils.redact import redact

log = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 10.0

#: Service control can wait on a human typing a password into the polkit
#: agent, so it needs far longer than a status probe.
CONTROL_TIMEOUT_SECONDS = 120.0

# Distinguishable from any real exit status, which is 0-255.
RETURNCODE_NOT_EXECUTED = -1
RETURNCODE_TIMED_OUT = -2


@dataclass(frozen=True)
class CommandResult:
    """The outcome of one command.

    A non-zero exit status is a result, not an exception: callers decide what a
    failure means. Only a programming error raises.
    """

    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    executed: bool = True

    #: Human-readable reason the command could not run, when it could not run.
    error: str = ""

    @property
    def ok(self) -> bool:
        """True when the command ran to completion and reported success."""
        return self.executed and not self.timed_out and self.returncode == 0

    @property
    def command(self) -> str:
        """The command as a readable string, for logs and detail views."""
        return " ".join(self.argv)

    def output(self) -> str:
        """Combined stdout and stderr, stripped, for display to a user."""
        combined = "\n".join(part for part in (self.stdout.strip(), self.stderr.strip()) if part)
        return combined.strip()


def run(
    argv: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    input_text: str | None = None,
) -> CommandResult:
    """Execute ``argv`` and return its outcome.

    Args:
        argv: Program and arguments. Must be a non-empty sequence of strings.
        timeout: Seconds to wait before giving up and killing the process.
        input_text: Written to the command's stdin. Use this, not ``argv``, for
            anything sensitive.

    Raises:
        ValueError: If ``argv`` is empty or holds a non-string, which would mean
            a bug in the caller rather than a system condition.
    """
    command = tuple(argv)
    if not command:
        raise ValueError("argv must not be empty")
    if not all(isinstance(part, str) for part in command):
        raise ValueError(f"argv must contain only strings, got {command!r}")

    log.debug("Running: %s", redact(" ".join(command)))

    try:
        # stdin is closed unless the caller has something to send. Inheriting
        # the terminal would let a command block on an unexpected prompt.
        if input_text is None:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        else:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                input=input_text,
                check=False,
            )
    except subprocess.TimeoutExpired:
        log.warning("Timed out after %ss: %s", timeout, redact(" ".join(command)))
        return CommandResult(
            argv=command,
            returncode=RETURNCODE_TIMED_OUT,
            timed_out=True,
            error=f"The command did not finish within {timeout:g} seconds.",
        )
    except FileNotFoundError:
        log.debug("Not found: %s", command[0])
        return CommandResult(
            argv=command,
            returncode=RETURNCODE_NOT_EXECUTED,
            executed=False,
            error=f"{command[0]} is not installed or not on PATH.",
        )
    except PermissionError:
        log.debug("Not permitted to execute: %s", command[0])
        return CommandResult(
            argv=command,
            returncode=RETURNCODE_NOT_EXECUTED,
            executed=False,
            error=f"Not permitted to execute {command[0]}.",
        )
    except OSError as error:
        log.warning("Could not execute %s: %s", command[0], error)
        return CommandResult(
            argv=command,
            returncode=RETURNCODE_NOT_EXECUTED,
            executed=False,
            error=f"Could not execute {command[0]}: {error}",
        )

    result = CommandResult(
        argv=command,
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )
    if not result.ok:
        log.debug(
            "Exit %s from %s: %s",
            result.returncode,
            redact(command[0]),
            redact(result.stderr.strip()),
        )
    return result
