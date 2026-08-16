"""One consistent view of the system, gathered in a single pass."""

from __future__ import annotations

from dataclasses import dataclass

from yakg.models.connection import ConnectionState
from yakg.models.installation import Installation
from yakg.models.service import ServiceState


@dataclass(frozen=True)
class SystemSnapshot:
    """Everything the main window needs in order to draw itself.

    The user interface refreshes from a snapshot rather than querying each
    backend separately, so it can never show a half-updated mixture of an old
    and a new reading.
    """

    installation: Installation
    service: ServiceState
    connection: ConnectionState = ConnectionState.UNKNOWN

    @property
    def usable(self) -> bool:
        """True when the client is installed and its service was found."""
        return self.installation.installed and self.service.exists
