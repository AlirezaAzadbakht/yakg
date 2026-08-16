"""Privileged entry point for reading and writing the Kerio configuration.

This module is invoked by ``pkexec`` as ``/usr/libexec/yakg-helper``. It is
the only process YAKG uses to touch ``/etc/kerio-kvc.conf``. Verbs are
``read``, ``write`` and ``restore``. There are no path arguments: ``write``
reads XML from stdin, and ``restore`` takes a basename that must match
``kerio-kvc.conf.*`` under the backup directory.

Must not import GTK.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from yakg.backend.config import (
    MAX_CONFIG_BYTES,
    ConfigError,
    restore_from_backup,
    validate_backup_name,
    write_config_atomically,
)
from yakg.backend.constants import CONFIG_BACKUP_DIR, CONFIG_PATH

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_MISSING = 3

_USAGE = "usage: yakg-helper read|write|restore [backup-name]"


def dispatch(
    args: Sequence[str],
    *,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    config_path: Path = CONFIG_PATH,
    backup_dir: Path = CONFIG_BACKUP_DIR,
) -> int:
    """Run one helper verb. Paths are injectable so tests never touch ``/etc``."""
    if not args:
        print(_USAGE, file=stderr)
        return EXIT_USAGE

    verb = args[0]
    try:
        if verb == "read":
            if len(args) != 1:
                print(_USAGE, file=stderr)
                return EXIT_USAGE
            return _cmd_read(stdout, stderr, config_path)
        if verb == "write":
            if len(args) != 1:
                print(_USAGE, file=stderr)
                return EXIT_USAGE
            return _cmd_write(stdin, stderr, config_path, backup_dir)
        if verb == "restore":
            if len(args) != 2:
                print(_USAGE, file=stderr)
                return EXIT_USAGE
            return _cmd_restore(args[1], config_path, backup_dir)
    except ConfigError as error:
        print(str(error), file=stderr)
        return EXIT_ERROR
    except OSError as error:
        print(f"Could not update the configuration: {error}", file=stderr)
        return EXIT_ERROR

    print(_USAGE, file=stderr)
    return EXIT_USAGE


def _cmd_read(stdout: TextIO, stderr: TextIO, config_path: Path) -> int:
    if not config_path.is_file():
        print("The Kerio configuration file is missing.", file=stderr)
        return EXIT_MISSING
    stdout.write(config_path.read_text(encoding="utf-8"))
    return EXIT_OK


def _cmd_write(stdin: TextIO, stderr: TextIO, config_path: Path, backup_dir: Path) -> int:
    xml = stdin.read()
    if not xml.strip():
        print("The configuration to write was empty.", file=stderr)
        return EXIT_ERROR
    if len(xml.encode("utf-8")) > MAX_CONFIG_BYTES:
        print("The configuration file is not valid.", file=stderr)
        return EXIT_ERROR
    write_config_atomically(config_path, xml, backup_dir=backup_dir)
    return EXIT_OK


def _cmd_restore(basename: str, config_path: Path, backup_dir: Path) -> int:
    # Validate before any path is built so ``../`` never reaches the filesystem
    # as a parent traversal, even for a moment.
    name = validate_backup_name(basename)
    restore_from_backup(name, config_path=config_path, backup_dir=backup_dir)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Process entry. Uses the live Kerio paths; tests call :func:`dispatch`."""
    args = sys.argv[1:] if argv is None else list(argv)
    return dispatch(args, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
