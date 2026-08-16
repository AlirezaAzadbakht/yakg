"""Detect a Kerio server certificate fingerprint the same way the vendor wizard does.

The wizard runs ``openssl s_client -connect host:port`` and then
``openssl x509 -fingerprint -md5 -noout``. YAKG does the same as two
argument-vector calls, never through a shell. Host and port are validated
before openssl sees them.
"""

from __future__ import annotations

import ipaddress
import re

from yakg.backend.config import ConfigError, validate_host, validate_port
from yakg.backend.process import run
from yakg.models.action import ActionOutcome
from yakg.utils.logging import get_logger

log = get_logger(__name__)

FINGERPRINT_TIMEOUT_SECONDS = 15.0

_FINGERPRINT = re.compile(r"(?:[0-9A-Fa-f]{2}:){15}[0-9A-Fa-f]{2}")
_BEGIN_CERT = "-----BEGIN CERTIFICATE-----"
_END_CERT = "-----END CERTIFICATE-----"

_DETECT_FAILURE = "Could not read the server certificate."


def connect_argument(host: str, port: int) -> str:
    """Build the single ``host:port`` argument for ``openssl s_client -connect``."""
    if _is_ipv6(host):
        inner = host[1:-1] if host.startswith("[") else host
        return f"[{inner}]:{port}"
    return f"{host}:{port}"


def extract_certificate_pem(text: str) -> str:
    """Return the first PEM certificate in ``text``, or an empty string."""
    start = text.find(_BEGIN_CERT)
    end = text.find(_END_CERT)
    if start < 0 or end < 0 or end < start:
        return ""
    return text[start : end + len(_END_CERT)] + "\n"


def parse_openssl_fingerprint(text: str) -> str:
    """Extract the colon-hex digest from ``openssl x509 -fingerprint`` output."""
    line = text.strip().splitlines()[0] if text.strip() else ""
    _, separator, value = line.partition("=")
    candidate = (value if separator else line).strip()
    match = _FINGERPRINT.fullmatch(candidate)
    if match is None:
        raise ConfigError(_DETECT_FAILURE)
    return match.group(0)


def detect_fingerprint(host: str, port: int | str) -> tuple[str, ActionOutcome]:
    """Return ``(fingerprint, outcome)`` for ``host:port``.

    On failure the fingerprint is empty. Does not write any configuration.
    """
    try:
        safe_host = validate_host(host)
        safe_port = validate_port(port)
    except ConfigError as error:
        return "", ActionOutcome(ok=False, message=str(error))

    target = connect_argument(safe_host, safe_port)
    probe_argv = ["openssl", "s_client", "-connect", target]
    if _is_hostname(safe_host):
        probe_argv.extend(["-servername", safe_host])

    probe = run(probe_argv, timeout=FINGERPRINT_TIMEOUT_SECONDS)
    pem = extract_certificate_pem(probe.stdout) or extract_certificate_pem(probe.output())
    if not pem:
        return "", ActionOutcome(
            ok=False,
            message=_DETECT_FAILURE,
            detail=probe.error or probe.output(),
        )

    digest = run(
        ["openssl", "x509", "-fingerprint", "-md5", "-noout"],
        timeout=FINGERPRINT_TIMEOUT_SECONDS,
        input_text=pem,
    )
    if not digest.ok:
        return "", ActionOutcome(
            ok=False,
            message=_DETECT_FAILURE,
            detail=digest.error or digest.output(),
        )

    try:
        fingerprint = parse_openssl_fingerprint(digest.stdout)
    except ConfigError as error:
        return "", ActionOutcome(
            ok=False,
            message=str(error),
            detail=digest.output(),
        )

    log.info("Detected certificate fingerprint for %s", target)
    return fingerprint, ActionOutcome(ok=True, message="Detected the server fingerprint.")


def _is_ipv6(host: str) -> bool:
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        ipaddress.IPv6Address(candidate)
    except ValueError:
        return False
    return True


def _is_hostname(host: str) -> bool:
    return not _is_ipv6(host) and ":" not in host
