"""Parse Kerio VPN connection state from debug.log / error.log.

The line format and the VpnFSM markers were verified against a real
``/var/log/kerio-kvc/debug.log``. See ``docs/kerio-facts.md``. This module
never opens the Kerio configuration file and never keeps a username.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from yakg.backend.constants import DEBUG_LOG_NAME, ERROR_LOG_NAME
from yakg.models.connection import ConnectionInfo
from yakg.utils.redact import redact

#: How much of a large log to read from the end. A 10k-line debug.log is
#: well under this; older sessions fall off the tail and that is intended.
DEFAULT_TAIL_BYTES = 256_000

_LINE = re.compile(r"^\[(?P<timestamp>[^\]]+)\]\s+(?:\{(?P<channel>[^}]+)\}\s+)?(?P<message>.*)$")
_FSM_STATUS = re.compile(r"status to Engine \((?P<status>\w+)\)")
_CONNECTING_TO = re.compile(r"connecting to (?P<host>[^,\s]+):(?P<port>\d+)")
_LOCAL_IP = re.compile(r"local IP (?P<ip>\d{1,3}(?:\.\d{1,3}){3})")
_STARTED = re.compile(r"Kerio Control VPN Client Service .+ started\.\s*$")
_LIBVPN_ERROR = re.compile(r"libVpn\(error (?P<code>\d+)\)")
_PAREN_ERROR = re.compile(r"\((\d{3})\)")

_ERROR_MESSAGES = {
    "145": "The VPN connection timed out.",
    "142": "The VPN server closed the connection.",
    "167": "Could not establish the VPN data tunnel. UDP traffic may be blocked.",
}


@dataclass(frozen=True)
class LogLine:
    """One parsed Kerio log line."""

    timestamp: str
    channel: str
    message: str
    raw: str


def parse_log_line(line: str) -> LogLine | None:
    """Parse ``[16/Aug/2026 11:29:55] {channel} message``. Returns None if not."""
    match = _LINE.match(line.rstrip("\n"))
    if not match:
        return None
    return LogLine(
        timestamp=match.group("timestamp"),
        channel=match.group("channel") or "",
        message=match.group("message"),
        raw=line.rstrip("\n"),
    )


def parse_fsm_status(message: str) -> str | None:
    """Return the token inside ``status to Engine (token)``, or None."""
    match = _FSM_STATUS.search(message)
    return match.group("status") if match else None


def parse_connecting_target(message: str) -> tuple[str, int] | None:
    """Return ``(host, port)`` from ``connecting to host:port``. Drops username."""
    match = _CONNECTING_TO.search(message)
    if not match:
        return None
    return match.group("host"), int(match.group("port"))


def parse_local_ip(message: str) -> str | None:
    """Return the tunnel address from ``Creating new VPN connection: local IP``."""
    if "Creating new VPN connection" not in message and "local IP" not in message:
        # The IPCONFIG line also contains "IP ="; only the vpnCore create line
        # is used as the address we show.
        return None
    if "Creating new VPN connection" not in message:
        return None
    match = _LOCAL_IP.search(message)
    return match.group("ip") if match else None


def map_error(text: str) -> str:
    """Turn a Kerio error line into a short human message.

    Unknown ``error N`` codes become a generic failure. An unrecognised line
    yields an empty string so the caller can keep looking.
    """
    lowered = text.lower()
    if "another instance" in lowered:
        return "Another Kerio VPN Client is already running."
    if "network is unreachable" in lowered:
        return "The VPN server is unreachable."

    code_match = _LIBVPN_ERROR.search(text) or _PAREN_ERROR.search(text)
    if code_match:
        code = code_match.group("code") if code_match.re is _LIBVPN_ERROR else code_match.group(1)
        if code in _ERROR_MESSAGES:
            return _ERROR_MESSAGES[code]
        if "error" in lowered or "timeout" in lowered or "closed" in lowered:
            return "The VPN connection failed."

    if "operation timeout" in lowered:
        return _ERROR_MESSAGES["145"]
    if "connection closed by a remote peer" in lowered:
        return _ERROR_MESSAGES["142"]
    if "unable to establish data tunnel" in lowered:
        return _ERROR_MESSAGES["167"]
    return ""


def read_log_tail(path: Path, *, max_bytes: int = DEFAULT_TAIL_BYTES) -> str:
    """Read the last ``max_bytes`` of ``path``. Missing files yield ``""``."""
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    try:
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(-max_bytes, 2)
            data = handle.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def interpret_log(text: str) -> ConnectionInfo:
    """Extract FSM status, server, address and error from a log tail.

    Only lines after the last ``started.`` marker count. If that marker is not
    in the tail, no FSM claim is made: the caller must fall back to service
    state alone.
    """
    lines = [parse_log_line(raw) for raw in text.splitlines()]
    parsed = [line for line in lines if line is not None]

    last_started = -1
    for index, line in enumerate(parsed):
        if _STARTED.search(line.message):
            last_started = index

    window = parsed[last_started + 1 :] if last_started >= 0 else []

    fsm = ""
    fsm_line = ""
    server: str | None = None
    port: int | None = None
    local_ip: str | None = None
    last_error = ""
    error_line = ""

    for line in window:
        status = parse_fsm_status(line.message)
        if status:
            fsm = status
            fsm_line = line.raw
        target = parse_connecting_target(line.message)
        if target:
            server, port = target
        address = parse_local_ip(line.message)
        if address:
            local_ip = address
        mapped = map_error(line.message)
        if mapped:
            last_error = mapped
            error_line = line.raw

    # A later successful connect clears the error from this session.
    if fsm == "connected":
        last_error = ""
        error_line = ""

    detail_source = fsm_line or error_line
    return ConnectionInfo(
        server=server,
        port=port,
        local_ip=local_ip if fsm == "connected" else None,
        last_error=last_error,
        fsm=fsm,
        detail=redact(detail_source) if detail_source else "",
    )


def read_connection_logs(log_dir: Path | None) -> ConnectionInfo:
    """Read the Kerio log directory and interpret the tails."""
    if log_dir is None:
        return ConnectionInfo()
    debug = read_log_tail(log_dir / DEBUG_LOG_NAME)
    info = interpret_log(debug)
    if info.last_error:
        return info
    error_text = read_log_tail(log_dir / ERROR_LOG_NAME, max_bytes=32_000)
    extra = ""
    for raw in reversed(error_text.splitlines()):
        line = parse_log_line(raw)
        message = line.message if line else raw
        extra = map_error(message)
        if extra:
            break
    if not extra:
        return info
    return ConnectionInfo(
        server=info.server,
        port=info.port,
        local_ip=info.local_ip,
        last_error=extra,
        fsm=info.fsm,
        detail=info.detail or redact(raw),
    )
