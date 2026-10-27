"""Named VPN profiles kept by YAKG, not by the Kerio client."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    """One connection YAKG can project into the single Kerio configuration.

    There is no password field. The secret store holds that, and the Kerio
    file receives it only as an ``XOR:`` blob at activate time.
    """

    id: str
    name: str
    server: str
    port: int
    username: str
    fingerprint: str
    connection_type: str = "persistent"
    #: IPv4 CIDRs kept on the tunnel. Empty means leave Kerio's routes alone.
    routes: tuple[str, ...] = ()

    def __repr__(self) -> str:
        # Username is not a secret, but it is PII and must not appear in logs.
        return (
            f"Profile(id={self.id!r}, name={self.name!r}, server={self.server!r}, port={self.port})"
        )

    @property
    def label(self) -> str:
        """Picker text: name, plus the server when we have one."""
        if self.server:
            return f"{self.name} — {self.server}"
        return self.name


@dataclass(frozen=True)
class ProfileState:
    """The profiles on disk and which one was last written to Kerio."""

    profiles: tuple[Profile, ...] = ()
    active_id: str | None = None

    def get(self, profile_id: str) -> Profile | None:
        """Return the profile with ``profile_id``, or ``None``."""
        for profile in self.profiles:
            if profile.id == profile_id:
                return profile
        return None
