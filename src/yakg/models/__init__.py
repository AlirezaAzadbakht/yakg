"""Plain data types shared between the backend and the user interface."""

from yakg.models.action import ActionOutcome
from yakg.models.connection import ConnectionState
from yakg.models.installation import Installation
from yakg.models.service import ServiceBackendKind, ServiceState
from yakg.models.snapshot import SystemSnapshot

__all__ = [
    "ActionOutcome",
    "ConnectionState",
    "Installation",
    "ServiceBackendKind",
    "ServiceState",
    "SystemSnapshot",
]
