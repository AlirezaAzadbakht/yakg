"""VPN connection state and the details parsed from the Kerio log."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ConnectionState(Enum):
    """Whether the VPN tunnel is up."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        """A short human-readable description."""
        return {
            ConnectionState.DISCONNECTED: "Disconnected",
            ConnectionState.CONNECTING: "Connecting…",
            ConnectionState.CONNECTED: "Connected",
            ConnectionState.UNKNOWN: "Unknown",
        }[self]


@dataclass(frozen=True)
class ConnectionInfo:
    """Tunnel state plus whatever the Kerio log can justify showing.

    ``state`` is decided by combining this with the service status, not by the
    log alone: a stale ``(connected)`` line from a previous run is not a live
    tunnel.
    """

    state: ConnectionState = ConnectionState.UNKNOWN
    server: str | None = None
    port: int | None = None
    local_ip: str | None = None
    last_error: str = ""
    fsm: str = ""
    detail: str = ""

    @property
    def label(self) -> str:
        return self.state.label

    @property
    def server_display(self) -> str:
        """Host, with port only when it is not the default."""
        if not self.server:
            return ""
        if self.port and self.port != 4090:
            return f"{self.server}:{self.port}"
        return self.server
