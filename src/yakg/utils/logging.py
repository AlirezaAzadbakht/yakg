"""Application logging setup.

YAKG logs to stderr and to a rotating file under the XDG state directory. Every
handler carries a redaction filter, so a secret cannot reach the disk even if a
future caller logs something careless.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

from yakg.utils.redact import redact

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_MAX_BYTES = 1_000_000
_BACKUP_COUNT = 3

_configured = False


class RedactingFilter(logging.Filter):
    """Rewrite each record's message with secrets removed.

    The record is collapsed to a fully formatted, redacted string. Deferred
    ``%``-style arguments are resolved here rather than at handler level,
    because a secret hiding in ``record.args`` would otherwise bypass the
    substitution entirely.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            # A malformed format string must never take the application down.
            message = str(record.msg)

        record.msg = redact(message)
        record.args = None

        if record.exc_text:
            record.exc_text = redact(record.exc_text)

        return True


def log_directory() -> Path:
    """Return the directory YAKG writes its log file to."""
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return base / "yakg"


def configure_logging(*, level: int = logging.INFO, log_to_file: bool = True) -> None:
    """Install YAKG's handlers on the root logger.

    Safe to call more than once; only the first call has an effect.
    """
    global _configured
    if _configured:
        return

    formatter = logging.Formatter(_LOG_FORMAT)
    redacting = RedactingFilter()

    root = logging.getLogger()
    root.setLevel(level)

    stderr_handler = logging.StreamHandler()
    stderr_handler.setFormatter(formatter)
    stderr_handler.addFilter(redacting)
    root.addHandler(stderr_handler)

    if log_to_file:
        directory = log_directory()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                directory / "yakg.log",
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
        except OSError as error:
            # An unwritable home directory is not a reason to refuse to start.
            root.warning("Could not open the log file in %s: %s", directory, error)
        else:
            file_handler.setFormatter(formatter)
            file_handler.addFilter(redacting)
            root.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger below the ``yakg`` namespace."""
    return logging.getLogger(name)
