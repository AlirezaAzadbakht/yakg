"""Desktop secret store for profile passwords.

The user interface never sees a stored password except as a boolean
(``contains``). Values returned by ``get`` are for activate only and must
not be logged or put in an ``ActionOutcome`` message.

``LibsecretStore`` imports the Secret typelib only inside methods so the
backend stays importable in CI, which has no GObject stack.
"""

from __future__ import annotations

from typing import Any, Protocol

from yakg.models.action import ActionOutcome
from yakg.utils.logging import get_logger

log = get_logger(__name__)

NO_KEYRING_MESSAGE = "YAKG could not reach the desktop secret store."

SCHEMA_NAME = "io.github.yakg.Yakg"
_ATTRIBUTE = "profile-id"


class SecretStore(Protocol):
    """Lookup, store and delete a profile password."""

    def get(self, profile_id: str) -> str | None:
        """Return the password, or ``None`` if it is missing or unreachable."""

    def set(self, profile_id: str, password: str) -> ActionOutcome:
        """Store ``password`` for ``profile_id``."""

    def delete(self, profile_id: str) -> ActionOutcome:
        """Remove the password, if any."""

    def contains(self, profile_id: str) -> bool:
        """True when a password is stored for ``profile_id``."""

    def reachable(self) -> bool:
        """True when the store can be contacted."""


class MemorySecretStore:
    """In-process store for tests. Never talks to the session keyring."""

    def __init__(self) -> None:
        self._items: dict[str, str] = {}

    def get(self, profile_id: str) -> str | None:
        return self._items.get(profile_id)

    def set(self, profile_id: str, password: str) -> ActionOutcome:
        self._items[profile_id] = password
        return ActionOutcome(ok=True)

    def delete(self, profile_id: str) -> ActionOutcome:
        self._items.pop(profile_id, None)
        return ActionOutcome(ok=True)

    def contains(self, profile_id: str) -> bool:
        return profile_id in self._items

    def reachable(self) -> bool:
        return True


class LibsecretStore:
    """GNOME Keyring (or another Secret Service) via libsecret."""

    def __init__(self) -> None:
        self._failed = False

    def get(self, profile_id: str) -> str | None:
        secret = _secret_module()
        if secret is None:
            self._failed = True
            return None
        try:
            schema = _schema(secret)
            value = secret.password_lookup_sync(schema, {_ATTRIBUTE: profile_id}, None)
        except Exception:
            self._failed = True
            log.info("Could not read a profile password from the secret store")
            return None
        self._failed = False
        return str(value) if value else None

    def set(self, profile_id: str, password: str) -> ActionOutcome:
        secret = _secret_module()
        if secret is None:
            self._failed = True
            return ActionOutcome(ok=False, message=NO_KEYRING_MESSAGE)
        try:
            schema = _schema(secret)
            ok = secret.password_store_sync(
                schema,
                {_ATTRIBUTE: profile_id},
                secret.COLLECTION_DEFAULT,
                f"YAKG profile {profile_id}",
                password,
                None,
            )
        except Exception as error:
            self._failed = True
            log.info("Could not store a profile password")
            return ActionOutcome(
                ok=False,
                message=NO_KEYRING_MESSAGE,
                detail=str(error),
            )
        if not ok:
            self._failed = True
            return ActionOutcome(ok=False, message=NO_KEYRING_MESSAGE)
        self._failed = False
        return ActionOutcome(ok=True)

    def delete(self, profile_id: str) -> ActionOutcome:
        secret = _secret_module()
        if secret is None:
            return ActionOutcome(ok=True)
        try:
            schema = _schema(secret)
            secret.password_clear_sync(schema, {_ATTRIBUTE: profile_id}, None)
        except Exception as error:
            log.info("Could not delete a profile password")
            return ActionOutcome(
                ok=False,
                message=NO_KEYRING_MESSAGE,
                detail=str(error),
            )
        return ActionOutcome(ok=True)

    def contains(self, profile_id: str) -> bool:
        return self.get(profile_id) is not None

    def reachable(self) -> bool:
        if self._failed:
            return False
        return _secret_module() is not None


def default_secret_store() -> SecretStore:
    """The session keyring, when the Secret typelib is available."""
    return LibsecretStore()


def _secret_module() -> Any:
    try:
        import gi

        gi.require_version("Secret", "1")
        from gi.repository import Secret
    except (ImportError, ValueError):
        return None
    return Secret


def _schema(secret: Any) -> Any:
    return secret.Schema.new(
        SCHEMA_NAME,
        secret.SchemaFlags.NONE,
        {_ATTRIBUTE: secret.SchemaAttributeType.STRING},
    )
