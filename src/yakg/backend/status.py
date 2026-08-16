"""Gathering of a complete system snapshot.

This is the one call the user interface makes to find out what is going on.
Keeping it here, rather than in the window, is what lets the whole detection
path be exercised from a terminal and from tests.
"""

from __future__ import annotations

from yakg.backend.detect import detect_client
from yakg.backend.service import ServiceManager
from yakg.models.connection import ConnectionState
from yakg.models.installation import Installation
from yakg.models.service import ServiceState
from yakg.models.snapshot import SystemSnapshot
from yakg.utils.logging import get_logger

log = get_logger(__name__)


def infer_connection_state(installation: Installation, service: ServiceState) -> ConnectionState:
    """Infer as much about the tunnel as can be justified.

    The Kerio daemon establishes the connection itself, from its configuration,
    as soon as it starts; there is no separate connect step (see
    ``docs/kerio-facts.md``). So a stopped service means there is definitely no
    tunnel, and that much can be reported confidently.

    The converse does not hold. A running daemon may still be retrying, or
    failing to authenticate. Confirming a live tunnel needs connection facts
    that have not been verified yet, so this reports ``UNKNOWN`` instead of
    guessing. Milestone 3 replaces this function with a real check.
    """
    if not installation.installed or not service.exists:
        return ConnectionState.UNKNOWN
    if service.active is False:
        return ConnectionState.DISCONNECTED
    return ConnectionState.UNKNOWN


def gather_status(manager: ServiceManager | None = None) -> SystemSnapshot:
    """Detect the client, the service and, as far as possible, the connection.

    Blocking. Callers on the GTK main thread must run this on a worker thread.
    """
    installation = detect_client()
    service = (manager if manager is not None else ServiceManager()).get_service_status()
    connection = infer_connection_state(installation, service)

    return SystemSnapshot(
        installation=installation,
        service=service,
        connection=connection,
    )
