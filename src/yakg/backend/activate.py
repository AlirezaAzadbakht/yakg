"""Project a YAKG profile into the single Kerio configuration file."""

from __future__ import annotations

from collections.abc import Callable

from yakg.backend.config import ConfigError, KerioConfig, generate_config, xor_decode
from yakg.backend.config_access import ConfigReadResult, read_config, write_config
from yakg.backend.profiles import (
    ProfileStore,
    default_store,
    list_profiles,
    new_profile_id,
    save_profile,
    set_active_id,
    validate_profile_name,
)
from yakg.backend.secrets import NO_KEYRING_MESSAGE, SecretStore, default_secret_store
from yakg.backend.service import ServiceManager
from yakg.models.action import ActionOutcome
from yakg.models.installation import Installation
from yakg.models.profile import Profile
from yakg.utils.logging import get_logger

log = get_logger(__name__)

MISSING_PASSWORD_MESSAGE = "This profile has no saved password. Open Settings and set one."  # noqa: S105

WriteConfig = Callable[[str], ActionOutcome]
ReadConfig = Callable[[], ConfigReadResult]


def resolve_profile_password(secrets: SecretStore, profile_id: str) -> tuple[str, ActionOutcome]:
    """Look up a profile password, or a classified failure if we cannot.

    Callers must use the outcome: a missing keyring is not the same as a
    profile that was never given a password.
    """
    password = secrets.get(profile_id) or ""
    if password:
        return password, ActionOutcome(ok=True)
    if not secrets.reachable():
        return "", ActionOutcome(ok=False, message=NO_KEYRING_MESSAGE)
    return "", ActionOutcome(ok=False, message=MISSING_PASSWORD_MESSAGE)


def profile_to_config(profile: Profile, password: str) -> KerioConfig:
    """Build the Kerio config we will write for ``profile``."""
    return KerioConfig(
        server=profile.server,
        port=profile.port,
        username=profile.username,
        password_xor="",
        fingerprint=profile.fingerprint,
        password_plain=password,
        connection_type=profile.connection_type,
    )


def activate_profile(
    profile: Profile,
    password: str,
    *,
    store: ProfileStore | None = None,
    write: WriteConfig = write_config,
    manager: ServiceManager | None = None,
) -> ActionOutcome:
    """Write ``profile`` into Kerio and record it as active.

    Reloads the service only when it is already running. The password is used
    to build XML and is never returned or logged.
    """
    if not password:
        return ActionOutcome(ok=False, message=MISSING_PASSWORD_MESSAGE)

    try:
        xml = generate_config(profile_to_config(profile, password))
    except ConfigError as error:
        return ActionOutcome(ok=False, message=str(error))

    outcome = write(xml)
    if not outcome.ok:
        return outcome

    set_active_id(store if store is not None else default_store(), profile.id)
    log.info("Activated profile %s for %s:%s", profile.name, profile.server, profile.port)
    applied = ActionOutcome(ok=True, message="Applied the profile.", detail=outcome.detail)
    return reload_if_running(applied, manager=manager)


def reload_if_running(
    saved: ActionOutcome,
    *,
    manager: ServiceManager | None = None,
) -> ActionOutcome:
    """Reload the Kerio service after a successful write, if it is up."""
    backend = manager if manager is not None else ServiceManager()
    service = backend.get_service_status()
    if service.active is not True:
        return saved

    reloaded = backend.reload_service()
    if reloaded.ok:
        return saved
    if reloaded.cancelled:
        return ActionOutcome(
            ok=True,
            message="Applied the profile. The service was not reloaded.",
            detail=reloaded.detail,
        )
    return ActionOutcome(
        ok=True,
        message="Applied the profile, but could not reload the service.",
        detail=reloaded.detail,
    )


def import_from_kerio(
    config: KerioConfig,
    *,
    store: ProfileStore,
    secrets: SecretStore,
) -> Profile:
    """Create a profile from a live Kerio config and seed the secret store.

    ``xor_decode`` runs here, in the backend, so the user is not asked to
    retype a password we already had to read. The UI must not call it.
    """
    name = _import_name(config.server)
    profile = save_profile(
        store,
        Profile(
            id=new_profile_id(),
            name=name,
            server=config.server,
            port=config.port,
            username=config.username,
            fingerprint=config.fingerprint,
            connection_type=config.connection_type,
        ),
    )
    if config.password_xor:
        try:
            plaintext = xor_decode(config.password_xor)
        except ConfigError:
            plaintext = ""
        if plaintext:
            secrets.set(profile.id, plaintext)
    set_active_id(store, profile.id)
    log.info("Imported a profile from the Kerio configuration for %s", profile.server)
    return profile


def maybe_import_first_profile(
    installation: Installation,
    *,
    store: ProfileStore | None = None,
    secrets: SecretStore | None = None,
    read: ReadConfig = read_config,
) -> ActionOutcome:
    """Import the live Kerio file when YAKG has no profiles yet.

    Cancelled polkit is returned as-is so the caller can stay silent. A
    missing helper is a non-ok outcome; the window must not toast that on
    a background refresh.
    """
    disk = store if store is not None else default_store()
    if list_profiles(disk):
        return ActionOutcome(ok=True)
    if not installation.config_present:
        return ActionOutcome(ok=True)

    result = read()
    if result.outcome.cancelled:
        return result.outcome
    if not result.outcome.ok:
        return result.outcome
    if result.missing or result.config is None:
        return ActionOutcome(ok=True)

    import_from_kerio(
        result.config,
        store=disk,
        secrets=secrets if secrets is not None else default_secret_store(),
    )
    return ActionOutcome(ok=True)


def _import_name(server: str) -> str:
    try:
        return validate_profile_name(server)
    except ConfigError:
        return "Imported"
