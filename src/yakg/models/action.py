"""The result of a privileged service action.

Control operations return this instead of raising, so a cancelled password
prompt can be distinguished from a real failure. The user interface treats
``cancelled`` as a choice, not an error: it must not show an error toast.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionOutcome:
    """What happened when YAKG asked the init system to change something."""

    ok: bool

    #: True when the user dismissed the authentication prompt. Not a failure
    #: to report: they decided not to proceed.
    cancelled: bool = False

    #: Human-readable, safe for a toast. Empty when ``cancelled``.
    message: str = ""

    #: Raw command output, for the technical details view. Never shown as the
    #: primary error.
    detail: str = ""
