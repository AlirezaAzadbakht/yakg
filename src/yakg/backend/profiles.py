"""User-owned profile files under the XDG config directory.

A profile JSON never contains a password. If a file was edited to include
one, it is dropped on read and not written back.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yakg.backend.config import (
    BACKUP_DIR_MODE,
    FILE_MODE,
    ConfigError,
    validate_fingerprint,
    validate_host,
    validate_port,
    write_private_file,
)
from yakg.backend.paths import config_home
from yakg.backend.routes import parse_routes
from yakg.models.profile import Profile, ProfileState
from yakg.utils.logging import get_logger

log = get_logger(__name__)

_SECRET_KEYS = frozenset({"password", "password_xor", "password_plain", "passwd", "pwd", "secret"})


@dataclass(frozen=True)
class ProfileStore:
    """Root of YAKG's own config tree. Tests pass a temporary directory."""

    root: Path

    @property
    def profiles_dir(self) -> Path:
        return self.root / "profiles"

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"


def default_store() -> ProfileStore:
    """The on-disk store under the current XDG config home."""
    return ProfileStore(config_home())


def validate_profile_id(value: str) -> str:
    """Return ``value`` if it is a canonical UUID string."""
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise ConfigError("That profile is not valid.") from error
    if str(parsed) != value:
        raise ConfigError("That profile is not valid.")
    return value


def validate_profile_name(name: str) -> str:
    """Return a non-empty profile name that cannot be used as a path."""
    text = name.strip()
    if not text:
        raise ConfigError("The profile name is missing.")
    if any(character in text for character in "/\\\0") or ".." in text:
        raise ConfigError("The profile name is not valid.")
    return text


def validate_username(username: str) -> str:
    """Return a non-empty username without shell metacharacters."""
    text = username.strip()
    if not text:
        raise ConfigError("The username is missing.")
    if any(character in ";|&\n\r" for character in text):
        raise ConfigError("The username is not valid.")
    return text


def validate_profile(profile: Profile) -> Profile:
    """Validate every field we will later project into Kerio."""
    validate_profile_id(profile.id)
    name = validate_profile_name(profile.name)
    server = validate_host(profile.server)
    port = validate_port(profile.port)
    username = validate_username(profile.username)
    fingerprint = validate_fingerprint(profile.fingerprint)
    return Profile(
        id=profile.id,
        name=name,
        server=server,
        port=port,
        username=username,
        fingerprint=fingerprint,
        connection_type=profile.connection_type or "persistent",
        routes=parse_routes(" ".join(profile.routes)),
    )


def new_profile_id() -> str:
    """Return a fresh canonical UUID for a new profile."""
    return str(uuid.uuid4())


def list_profiles(store: ProfileStore) -> tuple[Profile, ...]:
    """Return every readable profile, sorted by name then id."""
    directory = store.profiles_dir
    if not directory.is_dir():
        return ()
    found: list[Profile] = []
    for path in sorted(directory.glob("*.json")):
        try:
            found.append(_read_profile_file(path))
        except ConfigError:
            log.warning("Skipping unreadable profile file %s", path.name)
    found.sort(key=lambda item: (item.name.lower(), item.id))
    return tuple(found)


def get_profile(store: ProfileStore, profile_id: str) -> Profile | None:
    """Return one profile, or ``None`` if it is not on disk."""
    try:
        validate_profile_id(profile_id)
    except ConfigError:
        return None
    path = store.profiles_dir / f"{profile_id}.json"
    if not path.is_file():
        return None
    try:
        return _read_profile_file(path)
    except ConfigError:
        return None


def save_profile(store: ProfileStore, profile: Profile) -> Profile:
    """Validate and atomically write ``profile``. Returns the stored value."""
    stored = validate_profile(profile)
    _ensure_dirs(store)
    path = store.profiles_dir / f"{stored.id}.json"
    payload = {
        "id": stored.id,
        "name": stored.name,
        "server": stored.server,
        "port": stored.port,
        "username": stored.username,
        "fingerprint": stored.fingerprint,
        "connection_type": stored.connection_type,
        "routes": list(stored.routes),
    }
    _write_json(path, payload)
    log.info("Saved profile %s for %s:%s", stored.name, stored.server, stored.port)
    return stored


def delete_profile(store: ProfileStore, profile_id: str) -> None:
    """Remove a profile file. Missing files are not an error."""
    validate_profile_id(profile_id)
    path = store.profiles_dir / f"{profile_id}.json"
    if path.is_file():
        path.unlink()
    if get_active_id(store) == profile_id:
        set_active_id(store, None)
    log.info("Deleted profile %s", profile_id)


def get_active_id(store: ProfileStore) -> str | None:
    """Return the last profile written to Kerio, if it still exists."""
    state = _read_state(store)
    active = state.get("active_id")
    if not isinstance(active, str) or not active:
        return None
    if get_profile(store, active) is None:
        return None
    return active


def set_active_id(store: ProfileStore, profile_id: str | None) -> None:
    """Record which profile was last projected into Kerio."""
    if profile_id is not None:
        validate_profile_id(profile_id)
    _ensure_dirs(store)
    _write_json(store.state_path, {"active_id": profile_id})


def load_profile_state(store: ProfileStore) -> ProfileState:
    """Profiles and the active id, without opening the Kerio file."""
    return ProfileState(profiles=list_profiles(store), active_id=get_active_id(store))


def _ensure_dirs(store: ProfileStore) -> None:
    store.root.mkdir(mode=BACKUP_DIR_MODE, parents=True, exist_ok=True)
    store.profiles_dir.mkdir(mode=BACKUP_DIR_MODE, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(store.root, BACKUP_DIR_MODE)
        os.chmod(store.profiles_dir, BACKUP_DIR_MODE)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(f"{path.name}.tmp")
    write_private_file(tmp, text.encode("utf-8"))
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        os.chmod(path, FILE_MODE)


def _read_state(store: ProfileStore) -> dict[str, Any]:
    if not store.state_path.is_file():
        return {}
    try:
        loaded = json.loads(store.state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _read_profile_file(path: Path) -> Profile:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError("That profile is not valid.") from error
    if not isinstance(raw, dict):
        raise ConfigError("That profile is not valid.")
    # Drop secrets so a hand-edited file cannot keep them in memory as part of
    # the profile, and so a later save will not write them back.
    for key in list(raw):
        value = raw.get(key)
        if (
            key in _SECRET_KEYS
            or str(key).startswith("XOR")
            or (isinstance(value, str) and value.startswith("XOR:"))
        ):
            raw.pop(key, None)
    try:
        profile = Profile(
            id=str(raw["id"]),
            name=str(raw.get("name", "")),
            server=str(raw.get("server", "")),
            port=int(raw.get("port", 0)),
            username=str(raw.get("username", "")),
            fingerprint=str(raw.get("fingerprint", "")),
            connection_type=str(raw.get("connection_type") or "persistent"),
            routes=tuple(str(item) for item in raw.get("routes") or ()),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ConfigError("That profile is not valid.") from error
    return validate_profile(profile)
