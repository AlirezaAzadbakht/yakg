"""VPN connection state.

Only ``UNKNOWN`` is ever produced in Milestone 1. Determining a real connection
state requires facts about the Kerio client that have not been verified yet, and
those gaps are recorded in ``docs/kerio-facts.md``. The enum exists now so the
user interface can be written against its final shape.
"""

from __future__ import annotations

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
