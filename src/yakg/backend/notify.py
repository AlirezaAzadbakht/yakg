"""Decide when a desktop notification should be sent.

No GTK. The UI turns a ``NotifyEvent`` into ``Gio.Notification``. Titles
and bodies never include a username or a password.
"""

from __future__ import annotations

from dataclasses import dataclass

from yakg.models.connection import ConnectionState
from yakg.models.snapshot import SystemSnapshot

TIMEOUT_TITLE = "Could not connect to the VPN"
CONNECTED_TITLE = "Connected"
DISCONNECTED_TITLE = "Disconnected"


@dataclass(frozen=True)
class NotifyEvent:
    """A desktop notification that is safe to show."""

    title: str
    body: str
    kind: str


def notification_body(snapshot: SystemSnapshot) -> str:
    """Profile name and server, never a username."""
    parts: list[str] = []
    state = snapshot.profiles
    profile = state.get(state.active_id) if state.active_id else None
    if profile is not None:
        parts.append(profile.name)
        if profile.server:
            parts.append(profile.server)
    elif snapshot.connection.server_display:
        parts.append(snapshot.connection.server_display)
    return " — ".join(parts)


def next_notification(
    previous: SystemSnapshot | None,
    current: SystemSnapshot,
    *,
    window_visible: bool,
    user_stop: bool,
    timeout: bool = False,
) -> NotifyEvent | None:
    """Return the notification for this transition, or ``None``.

    No event on an unchanged state, on ``CONNECTING``, or on the first
    visible launch. A hidden first snapshot notifies only when already
    connected. A user Disconnect/Cancel does not notify.
    """
    body = notification_body(current)
    if timeout:
        if window_visible:
            return None
        return NotifyEvent(title=TIMEOUT_TITLE, body=body, kind="timeout")

    current_state = current.connection.state
    if previous is None:
        if window_visible or current_state is not ConnectionState.CONNECTED:
            return None
        return NotifyEvent(title=CONNECTED_TITLE, body=body, kind="connected")

    previous_state = previous.connection.state
    if previous_state is current_state:
        return None
    if current_state is ConnectionState.CONNECTING:
        return None

    if current_state is ConnectionState.CONNECTED:
        if window_visible:
            return None
        return NotifyEvent(title=CONNECTED_TITLE, body=body, kind="connected")

    if previous_state is ConnectionState.CONNECTED and not user_stop:
        return NotifyEvent(title=DISCONNECTED_TITLE, body=body, kind="disconnected")
    return None
