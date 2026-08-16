"""Parse, validate and write Kerio Control VPN Client configuration.

The on-disk format is the XML the package ``postinst`` writes; see
``docs/kerio-facts.md``. The password field is
Kerio's ``XOR:`` hex blob (obfuscation, not encryption). This module will
decode that blob for tests, but the user interface must never call
:func:`xor_decode`.

Every function that touches a file takes an explicit path so tests never
open ``/etc/kerio-kvc.conf``.
"""

from __future__ import annotations

import contextlib
import ipaddress
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from yakg.backend.constants import DEFAULT_PORT
from yakg.utils.logging import get_logger

log = get_logger(__name__)

#: Same constant the package ``postinst`` uses: each password byte XOR 85.
XOR_KEY = 85

_XOR_BLOB = re.compile(r"^XOR:[0-9a-fA-F]+$")
_FINGERPRINT = re.compile(r"(?:[0-9A-Fa-f]{2}:){15}[0-9A-Fa-f]{2}\Z")
_BACKUP_NAME = re.compile(r"^kerio-kvc\.conf\.[A-Za-z0-9._-]+\Z")

#: Characters that must never appear in a host we later pass to openssl.
_HOST_FORBIDDEN = frozenset(";&|<>$`\\\"'(){}!*?# \t\n\r")

_KNOWN_CONNECTION_TAGS = frozenset(
    {"server", "port", "username", "password", "fingerprint", "active"}
)

#: Refuse a configuration larger than this. The vendor file is a few hundred
#: bytes; anything near a megabyte is not a Kerio config.
MAX_CONFIG_BYTES = 1_000_000

FILE_MODE = 0o600
BACKUP_DIR_MODE = 0o700


class ConfigError(ValueError):
    """A parse or validation failure that is safe to show to a person.

    Messages must not contain raw XML or a password.
    """


@dataclass(frozen=True)
class UnknownElement:
    """A ``<connection>`` child we do not interpret, kept so a write does not drop it."""

    tag: str
    text: str = ""
    attrib: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class KerioConfig:
    """One Kerio VPN connection, as YAKG understands it.

    ``password_xor`` is the on-disk ``XOR:`` blob. ``password_plain`` is set
    only when the user typed a new password; empty means keep the blob.
    """

    server: str
    port: int
    username: str
    password_xor: str = field(repr=False)
    fingerprint: str
    password_plain: str = field(default="", repr=False)
    connection_type: str = "persistent"
    unknown_children: tuple[UnknownElement, ...] = ()


def xor_encode(plaintext: str) -> str:
    """Return Kerio's ``XOR:`` hex form of ``plaintext``."""
    encoded = "".join(f"{byte ^ XOR_KEY:02x}" for byte in plaintext.encode("utf-8"))
    return f"XOR:{encoded}"


def xor_decode(blob: str) -> str:
    """Return the plaintext of a Kerio ``XOR:`` blob.

    Exists for tests and for a future need. The user interface must not call
    this: YAKG never displays the stored password.
    """
    if not _XOR_BLOB.fullmatch(blob):
        raise ConfigError("The password is not a Kerio XOR value.")
    hex_part = blob[4:]
    if len(hex_part) % 2:
        raise ConfigError("The password is not a Kerio XOR value.")
    try:
        raw = bytes(int(hex_part[i : i + 2], 16) ^ XOR_KEY for i in range(0, len(hex_part), 2))
    except ValueError as error:
        raise ConfigError("The password is not a Kerio XOR value.") from error
    return raw.decode("utf-8")


def validate_host(host: str) -> str:
    """Return ``host`` if it is a safe hostname or IP address.

    Rejects whitespace and shell metacharacters so the value can later be
    passed to ``openssl`` as a single argument. IPv6 literals are accepted
    only when :mod:`ipaddress` can parse them.
    """
    if not host or host != host.strip():
        raise ConfigError(
            "The server name is missing." if not host.strip() else "The server name is not valid."
        )
    if any(character in _HOST_FORBIDDEN or character.isspace() for character in host):
        raise ConfigError("The server name is not valid.")

    if host.startswith("[") and host.endswith("]") and len(host) > 2:
        try:
            ipaddress.IPv6Address(host[1:-1])
        except ValueError as error:
            raise ConfigError("The server name is not valid.") from error
        return host

    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError as error:
            raise ConfigError("The server name is not valid.") from error
        return host

    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,253}[A-Za-z0-9])?", host):
        raise ConfigError("The server name is not valid.")
    if ".." in host or host.startswith(".") or host.endswith("."):
        raise ConfigError("The server name is not valid.")
    return host


def validate_port(value: int | str) -> int:
    """Return ``value`` as an integer port in ``1..65535``."""
    if isinstance(value, str):
        text = value.strip()
        if not text or not text.isdigit():
            raise ConfigError("The port must be a number between 1 and 65535.")
        number = int(text)
    else:
        number = value
    if not 1 <= number <= 65535:
        raise ConfigError("The port must be a number between 1 and 65535.")
    return number


def validate_fingerprint(value: str) -> str:
    """Return ``value`` if it is a 16-octet MD5 colon-hex fingerprint."""
    text = value.strip()
    if not _FINGERPRINT.fullmatch(text):
        raise ConfigError("The fingerprint is not a valid MD5 fingerprint.")
    return text


def validate_backup_name(name: str) -> str:
    """Return ``name`` if it is a safe backup basename.

    The helper accepts only a basename matching ``kerio-kvc.conf.*``. Any
    ``/`` or ``..`` is rejected so a caller cannot point restore at another
    file.
    """
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ConfigError("That backup name is not allowed.")
    if not name.startswith("kerio-kvc.conf.") or name == "kerio-kvc.conf.":
        raise ConfigError("That backup name is not allowed.")
    if not _BACKUP_NAME.fullmatch(name):
        raise ConfigError("That backup name is not allowed.")
    return name


def validate_config(config: KerioConfig) -> None:
    """Raise :class:`ConfigError` if ``config`` is not safe to write."""
    validate_host(config.server)
    validate_port(config.port)
    if not config.username.strip():
        raise ConfigError("The username is missing.")
    if any(character in ";|&\n\r" for character in config.username):
        raise ConfigError("The username is not valid.")
    validate_fingerprint(config.fingerprint)
    if config.password_plain:
        return
    if not config.password_xor or not _XOR_BLOB.fullmatch(config.password_xor):
        raise ConfigError("The password is missing.")
    if (len(config.password_xor) - 4) % 2:
        raise ConfigError("The password is missing.")


def parse_config(xml: str) -> KerioConfig:
    """Parse vendor-shaped configuration XML.

    Uses the standard library parser, which does not resolve external
    entities, so a crafted file cannot pull in other files (XXE). One
    ``<connection>`` is required; extras are an error rather than a guess.
    """
    if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        raise ConfigError("The configuration file is not valid.")

    try:
        # Stdlib ElementTree does not resolve external entities. We also reject
        # a DOCTYPE above. defusedxml is a PyPI dependency YAKG does not take.
        root = ET.fromstring(xml)  # noqa: S314
    except ET.ParseError as error:
        raise ConfigError("The configuration file is not valid.") from error

    if root.tag != "config":
        raise ConfigError("The configuration file is not valid.")

    connections = root.find("connections")
    if connections is None:
        raise ConfigError("The configuration file has no connection.")

    items = list(connections.findall("connection"))
    if not items:
        raise ConfigError("The configuration file has no connection.")
    if len(items) > 1:
        raise ConfigError("The configuration file has more than one connection.")

    conn = items[0]
    server = _child_text(conn, "server")
    port_text = _child_text(conn, "port")
    username = _child_text(conn, "username")
    password = _child_text(conn, "password")
    fingerprint = _child_text(conn, "fingerprint")
    connection_type = conn.attrib.get("type", "persistent") or "persistent"

    unknown: list[UnknownElement] = []
    for child in list(conn):
        if child.tag in _KNOWN_CONNECTION_TAGS:
            continue
        unknown.append(
            UnknownElement(
                tag=child.tag,
                text=(child.text or "").strip(),
                attrib=tuple(child.attrib.items()),
            )
        )

    port = validate_port(port_text) if port_text else DEFAULT_PORT

    config = KerioConfig(
        server=server,
        port=port,
        username=username,
        password_xor=password,
        fingerprint=fingerprint,
        connection_type=connection_type,
        unknown_children=tuple(unknown),
    )
    validate_config(config)
    log.info("Parsed Kerio configuration for %s:%s", config.server, config.port)
    return config


def generate_config(config: KerioConfig) -> str:
    """Serialise ``config`` as the vendor XML.

    A new ``password_plain`` is encoded; otherwise the existing ``XOR:`` blob
    is reused. ``<active>`` is always ``1``: YAKG connects by starting the
    service, and writing ``0`` would fight that. Comments are not emitted;
    ElementTree cannot round-trip them and the vendor file has none.
    """
    validate_config(config)
    password = xor_encode(config.password_plain) if config.password_plain else config.password_xor

    root = ET.Element("config")
    connections = ET.SubElement(root, "connections")
    conn = ET.SubElement(connections, "connection", {"type": config.connection_type})
    ET.SubElement(conn, "server").text = config.server
    ET.SubElement(conn, "port").text = str(config.port)
    ET.SubElement(conn, "username").text = config.username
    ET.SubElement(conn, "password").text = password
    ET.SubElement(conn, "fingerprint").text = config.fingerprint
    ET.SubElement(conn, "active").text = "1"
    for extra in config.unknown_children:
        element = ET.SubElement(conn, extra.tag, dict(extra.attrib))
        element.text = extra.text or None

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode") + "\n"


def write_private_file(path: Path, data: bytes) -> None:
    """Create or replace ``path`` with mode ``0600``."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, FILE_MODE)
    try:
        os.fchmod(fd, FILE_MODE)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    finally:
        os.close(fd)


def write_config_atomically(
    path: Path,
    xml: str,
    *,
    backup_dir: Path,
    now: datetime | None = None,
    verify: Callable[[str], None] | None = None,
) -> Path | None:
    """Validate ``xml``, back up ``path`` if it exists, then replace it.

    On any failure after a backup was made, the backup is restored. Returns
    the backup path, or ``None`` when there was no previous file.

    ``verify`` defaults to parse-and-validate of the file after the replace.
    Tests inject a failing callable to exercise the restore path.
    """
    if len(xml.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise ConfigError("The configuration file is not valid.")

    validate_config(parse_config(xml))
    checker = verify if verify is not None else _verify_written

    _ensure_backup_dir(backup_dir)
    backup_path = _backup_existing(path, backup_dir, now=now)
    tmp_path = path.with_name(f"{path.name}.tmp")

    try:
        write_private_file(tmp_path, xml.encode("utf-8"))
        os.replace(tmp_path, path)
        os.chmod(path, FILE_MODE)
        checker(path.read_text(encoding="utf-8"))
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        if backup_path is not None:
            _copy_private(backup_path, path)
        elif path.exists() and backup_path is None:
            # We created this file and then failed to verify it.
            path.unlink()
        raise

    log.info("Wrote Kerio configuration to %s", path)
    return backup_path


def restore_from_backup(
    basename: str,
    *,
    config_path: Path,
    backup_dir: Path,
) -> None:
    """Replace ``config_path`` with the named backup under ``backup_dir``."""
    name = validate_backup_name(basename)
    source = backup_dir / name
    if not source.is_file():
        raise ConfigError("That backup was not found.")

    backup_root = backup_dir.resolve()
    try:
        resolved = source.resolve()
    except OSError as error:
        raise ConfigError("That backup was not found.") from error
    if not resolved.is_relative_to(backup_root):
        raise ConfigError("That backup name is not allowed.")

    xml = source.read_text(encoding="utf-8")
    validate_config(parse_config(xml))
    tmp_path = config_path.with_name(f"{config_path.name}.tmp")
    try:
        write_private_file(tmp_path, xml.encode("utf-8"))
        os.replace(tmp_path, config_path)
        os.chmod(config_path, FILE_MODE)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def _child_text(parent: ET.Element, tag: str) -> str:
    child = parent.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _verify_written(text: str) -> None:
    validate_config(parse_config(text))


def _ensure_backup_dir(backup_dir: Path) -> None:
    backup_dir.mkdir(mode=BACKUP_DIR_MODE, parents=True, exist_ok=True)
    # A directory we cannot chmod is still usable if we can write files.
    with contextlib.suppress(OSError):
        os.chmod(backup_dir, BACKUP_DIR_MODE)


def _backup_existing(
    path: Path,
    backup_dir: Path,
    *,
    now: datetime | None,
) -> Path | None:
    if not path.is_file():
        return None
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{path.name}.{stamp}"
    if backup_path.exists():
        stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = backup_dir / f"{path.name}.{stamp}"
    _copy_private(path, backup_path)
    return backup_path


def _copy_private(source: Path, destination: Path) -> None:
    write_private_file(destination, source.read_bytes())
