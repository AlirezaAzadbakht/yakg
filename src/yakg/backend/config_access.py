"""Unprivileged access to the Kerio configuration via pkexec and yakg-helper.

The GUI never opens ``/etc/kerio-kvc.conf``. It asks the helper, with a
fixed argument vector. Configuration text and any new password travel on
stdin, never in argv.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from yakg.backend.classify import classify_failure, outcome_from_result
from yakg.backend.config import ConfigError, KerioConfig, parse_config, validate_backup_name
from yakg.backend.constants import HELPER_PATH
from yakg.backend.process import CONTROL_TIMEOUT_SECONDS, run
from yakg.models.action import ActionOutcome
from yakg.utils.logging import get_logger

log = get_logger(__name__)

MISSING_HELPER_MESSAGE = (
    "Applying a profile needs the YAKG helper. "
    "Install it with packaging/install-helper.sh (see the README)."
)

_READ_FAILURE = "Could not read the configuration."
_WRITE_FAILURE = "Could not save the configuration."
_RESTORE_FAILURE = "Could not restore the configuration."

#: Helper exit status when the Kerio file is not there yet.
EXIT_MISSING = 3


@dataclass(frozen=True)
class ConfigReadResult:
    """Outcome of asking the helper to read the Kerio configuration."""

    config: KerioConfig | None
    outcome: ActionOutcome
    missing: bool = False


def helper_available(helper: Path = HELPER_PATH) -> bool:
    """True when the privileged helper is installed."""
    return helper.is_file()


def read_config(*, helper: Path = HELPER_PATH) -> ConfigReadResult:
    """Read and parse the Kerio configuration through the helper."""
    if not helper_available(helper):
        return ConfigReadResult(
            None,
            ActionOutcome(ok=False, message=MISSING_HELPER_MESSAGE),
        )

    result = run(
        ["pkexec", str(helper), "read"],
        timeout=CONTROL_TIMEOUT_SECONDS,
    )
    if result.returncode == EXIT_MISSING:
        return ConfigReadResult(None, ActionOutcome(ok=True), missing=True)
    if not result.ok:
        return ConfigReadResult(None, classify_failure(result, _READ_FAILURE))

    try:
        config = parse_config(result.stdout)
    except ConfigError as error:
        return ConfigReadResult(None, ActionOutcome(ok=False, message=str(error)))

    log.info("Read Kerio configuration for %s:%s", config.server, config.port)
    return ConfigReadResult(config, ActionOutcome(ok=True))


def write_config(xml: str, *, helper: Path = HELPER_PATH) -> ActionOutcome:
    """Write ``xml`` through the helper. ``xml`` is sent on stdin, never argv."""
    if not helper_available(helper):
        return ActionOutcome(ok=False, message=MISSING_HELPER_MESSAGE)

    result = run(
        ["pkexec", str(helper), "write"],
        timeout=CONTROL_TIMEOUT_SECONDS,
        input_text=xml,
    )
    return outcome_from_result(
        result,
        success_message="Saved the configuration.",
        failure_message=_WRITE_FAILURE,
    )


def restore_config(basename: str, *, helper: Path = HELPER_PATH) -> ActionOutcome:
    """Ask the helper to restore a backup. ``basename`` is validated first."""
    try:
        name = validate_backup_name(basename)
    except ConfigError as error:
        return ActionOutcome(ok=False, message=str(error))

    if not helper_available(helper):
        return ActionOutcome(ok=False, message=MISSING_HELPER_MESSAGE)

    result = run(
        ["pkexec", str(helper), "restore", name],
        timeout=CONTROL_TIMEOUT_SECONDS,
    )
    return outcome_from_result(
        result,
        success_message="Restored the configuration.",
        failure_message=_RESTORE_FAILURE,
    )
