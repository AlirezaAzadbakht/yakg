"""Turn a failed service-control command into an ActionOutcome.

Matching on stderr is a courtesy on top of the exit status, never the primary
signal: an unrecognised message still produces a correct failure. The strings
below were taken from the binaries on the reference machine
(``systemctl``, ``libsystemd.so.0``, ``pkexec``).
"""

from __future__ import annotations

from yakg.backend.process import CommandResult
from yakg.models.action import ActionOutcome

NO_AGENT_MESSAGE = "YAKG could not ask for administrator authentication."

#: systemd, when no polkit agent can prompt.
_INTERACTIVE_AUTH = "org.freedesktop.DBus.Error.InteractiveAuthorizationRequired"

#: systemctl, when polkit refused or the prompt was dismissed.
_ACCESS_DENIED = "Access denied"

#: pkexec, SysV path only.
_PKEXEC_DISMISSED = "Request dismissed"
_PKEXEC_NOT_AUTHORIZED = "Not authorized"
_PKEXEC_NO_AGENT = "No authentication agent found"


def classify_failure(result: CommandResult, failure_message: str) -> ActionOutcome:
    """Map a non-successful :class:`CommandResult` to an :class:`ActionOutcome`.

    ``failure_message`` is the human-readable toast for a genuine failure.
    Cancelled prompts produce an empty message so the interface stays silent.
    """
    output = result.output()
    combined = "\n".join(part for part in (output, result.error) if part)

    if result.timed_out:
        return ActionOutcome(
            ok=False,
            message=failure_message,
            detail=result.error or output,
        )

    if _INTERACTIVE_AUTH in combined or _PKEXEC_NO_AGENT in combined:
        return ActionOutcome(
            ok=False,
            cancelled=False,
            message=NO_AGENT_MESSAGE,
            detail=combined,
        )

    if _PKEXEC_DISMISSED in combined or _ACCESS_DENIED in combined:
        return ActionOutcome(ok=False, cancelled=True, message="", detail=combined)

    if _PKEXEC_NOT_AUTHORIZED in combined:
        return ActionOutcome(
            ok=False,
            cancelled=False,
            message=failure_message,
            detail=combined,
        )

    return ActionOutcome(
        ok=False,
        message=failure_message,
        detail=combined or result.error,
    )


def outcome_from_result(
    result: CommandResult,
    *,
    success_message: str,
    failure_message: str,
) -> ActionOutcome:
    """Wrap a command result as success or a classified failure."""
    if result.ok:
        return ActionOutcome(ok=True, message=success_message, detail=result.output())
    return classify_failure(result, failure_message)
