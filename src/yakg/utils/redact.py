"""Removal of secrets from anything YAKG is about to write down.

Kerio stores a recoverable password in ``/etc/kerio-kvc.conf`` (see
``docs/kerio-facts.md``), so configuration text, command output and error
messages can all carry credentials. Everything that reaches a log file or a
crash report passes through :func:`redact` first.

Redaction is a safety net, not a licence to be careless: prefer never putting a
secret into a log message in the first place.
"""

from __future__ import annotations

import re
from typing import Final

PLACEHOLDER: Final = "[REDACTED]"

_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # Kerio's obfuscated password form, e.g. "XOR:3a3b3c".
    (re.compile(r"XOR:[0-9a-fA-F]*"), f"XOR:{PLACEHOLDER}"),
    # Kerio configuration elements holding sensitive values.
    (
        re.compile(r"<password>.*?</password>", re.IGNORECASE | re.DOTALL),
        f"<password>{PLACEHOLDER}</password>",
    ),
    (
        re.compile(r"<fingerprint>.*?</fingerprint>", re.IGNORECASE | re.DOTALL),
        f"<fingerprint>{PLACEHOLDER}</fingerprint>",
    ),
    # key=value and key: value forms, quoted or bare, up to the end of the line.
    (
        re.compile(
            r"\b(password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key)"
            r"(\s*[:=]\s*)"
            r"(\"[^\"]*\"|'[^']*'|\S+)",
            re.IGNORECASE,
        ),
        rf"\1\2{PLACEHOLDER}",
    ),
    # PEM-encoded private key material.
    (
        re.compile(
            r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        PLACEHOLDER,
    ),
)


def redact(text: str) -> str:
    """Return ``text`` with any recognised secret replaced by a placeholder."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
