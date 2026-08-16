"""Command-line entry point.

The graphical stack is imported lazily so that ``--version`` and ``--detect``
keep working on a machine without GTK, and so that a missing PyGObject produces
an explanation instead of a traceback.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from yakg import APP_NAME, __version__
from yakg.models.snapshot import SystemSnapshot
from yakg.utils.logging import configure_logging, get_logger

log = get_logger(__name__)

_MISSING_GTK = (
    "YAKG needs the GTK 4 and libadwaita Python bindings, which do not appear "
    "to be installed.\n\n"
    "On Ubuntu:\n"
    "    sudo apt install python3-gi python3-gi-cairo "
    "gir1.2-gtk-4.0 gir1.2-adw-1\n"
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yakg",
        description=(
            f"{APP_NAME} - an unofficial desktop frontend for the Kerio Control VPN Client."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument(
        "--detect",
        action="store_true",
        help="print what YAKG detects on this system and exit, without starting the interface",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="enable debug logging",
    )
    return parser


def format_report(snapshot: SystemSnapshot) -> str:
    """Render a snapshot as plain text for ``--detect``.

    Useful for troubleshooting over a terminal, and on headless machines where
    the interface cannot be started at all.
    """
    installation = snapshot.installation
    service = snapshot.service

    lines = [
        f"{APP_NAME} {__version__} detection report",
        "",
        "Kerio Control VPN Client",
        f"  Installed:      {'yes' if installation.installed else 'no'}",
        f"  Version:        {installation.version_display}",
        f"  Executable:     {installation.executable or 'not found'}",
        f"  Detected via:   {installation.detected_via or 'not detected'}",
        f"  Configuration:  {installation.config_path or 'not found'}",
        f"  Logs:           {installation.log_dir or 'not found'}",
        "",
        "Service",
        f"  Found:          {'yes' if service.exists else 'no'}",
        f"  Init system:    {service.backend.label}",
        f"  Unit:           {service.unit_name or 'not found'}",
        f"  Running:        {service.running_label}",
        f"  Start at boot:  {service.boot_label}",
        "",
        "Connection",
        f"  State:          {snapshot.connection.label}",
    ]

    notes = [note for note in (installation.detail, service.detail) if note]
    if notes:
        lines.extend(["", "Notes"])
        lines.extend(f"  {note}" for note in notes)

    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Run YAKG. Returns the process exit status."""
    args = _build_parser().parse_args(argv)

    configure_logging(level=logging.DEBUG if args.debug else logging.INFO)
    log.info("Starting %s %s", APP_NAME, __version__)

    if args.detect:
        from yakg.backend.status import gather_status

        print(format_report(gather_status()))
        return 0

    try:
        from yakg.app import YakgApplication
    except (ImportError, ValueError) as error:
        # ValueError is what gi.require_version raises for a missing typelib.
        log.error("The graphical stack is unavailable: %s", error)
        print(_MISSING_GTK, file=sys.stderr)
        return 1

    # GTK argument parsing is bypassed: YAKG's own parser has already run.
    return int(YakgApplication().run([]))


if __name__ == "__main__":
    raise SystemExit(main())
