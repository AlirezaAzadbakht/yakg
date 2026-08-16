"""Gathering of a complete system snapshot.

This is the one call the user interface makes to find out what is going on.
Keeping it here, rather than in the window, is what lets the whole detection
path be exercised from a terminal and from tests.
"""

from __future__ import annotations

from yakg.backend.connection import read_connection_logs
from yakg.backend.detect import detect_client
from yakg.backend.service import ServiceManager
from yakg.models.connection import ConnectionInfo, ConnectionState
from yakg.models.installation import Installation
from yakg.models.service import ServiceState
from yakg.models.snapshot import SystemSnapshot
from yakg.utils.logging import get_logger

log = get_logger(__name__)


def infer_connection_state(
    installation: Installation,
    service: ServiceState,
    info: ConnectionInfo | None = None,
) -> ConnectionInfo:
    """Combine service liveness with the last VpnFSM status.

    A stopped service is disconnected even if the log still says ``(connected)``
    from a previous run. A running service whose last FSM status is ``error``
    is still connecting: the daemon retries every 30 seconds.
    """
    parsed = info if info is not None else ConnectionInfo()

    if not installation.installed:
        return ConnectionInfo(state=ConnectionState.UNKNOWN, detail=parsed.detail)

    if not service.exists:
        return ConnectionInfo(state=ConnectionState.UNKNOWN, detail=parsed.detail)

    if service.active is False:
        return ConnectionInfo(
            state=ConnectionState.DISCONNECTED,
            server=parsed.server,
            port=parsed.port,
            last_error=parsed.last_error,
            fsm=parsed.fsm,
            detail=parsed.detail,
        )

    if service.active is None:
        return ConnectionInfo(
            state=ConnectionState.UNKNOWN,
            server=parsed.server,
            port=parsed.port,
            last_error=parsed.last_error,
            fsm=parsed.fsm,
            detail=parsed.detail,
        )

    if parsed.fsm == "connected":
        state = ConnectionState.CONNECTED
        local_ip = parsed.local_ip
        last_error = ""
    else:
        # running + connected-not-yet, disconnecting, error, or disconnected
        # (the daemon reconnects while persistent/active). All of these are
        # Connecting from the user's point of view.
        state = ConnectionState.CONNECTING
        local_ip = None
        last_error = parsed.last_error

    return ConnectionInfo(
        state=state,
        server=parsed.server,
        port=parsed.port,
        local_ip=local_ip,
        last_error=last_error,
        fsm=parsed.fsm,
        detail=parsed.detail,
    )


def gather_status(manager: ServiceManager | None = None) -> SystemSnapshot:
    """Detect the client, the service and the connection.

    Blocking. Callers on the GTK main thread must run this on a worker thread.
    Never opens the Kerio configuration file.
    """
    installation = detect_client()
    service = (manager if manager is not None else ServiceManager()).get_service_status()
    parsed = read_connection_logs(installation.log_dir)
    connection = infer_connection_state(installation, service, parsed)

    log.info(
        "Connection: state=%s fsm=%s server=%s",
        connection.state.value,
        connection.fsm or "-",
        connection.server or "-",
    )

    return SystemSnapshot(
        installation=installation,
        service=service,
        connection=connection,
    )
