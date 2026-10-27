"""Split tunnel: keep only the configured subnets on the Kerio tunnel.

Kerio pushes ``0.0.0.0/1`` and ``128.0.0.0/1`` when the server sets its default
route flag, which sends all traffic through the VPN. After a connect YAKG asks
the helper to drop those two routes and add the profile's own subnets instead.
"""

from __future__ import annotations

import ipaddress
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from yakg.backend.classify import outcome_from_result
from yakg.backend.config import ConfigError
from yakg.backend.constants import HELPER_PATH
from yakg.backend.process import CONTROL_TIMEOUT_SECONDS, CommandResult, run
from yakg.models.action import ActionOutcome
from yakg.utils.logging import get_logger

log = get_logger(__name__)

#: Tunnel interface created by the Kerio client (seen in ``ip -br link``).
TUNNEL_DEVICE = "kvnet"

#: The two routes Kerio uses to hijack the default route.
HIJACK_ROUTES = ("0.0.0.0/1", "128.0.0.0/1")

#: Kerio adds the hijack routes about a second after the state flips to
#: connected, so wait for them briefly before deleting.
HIJACK_WAIT_SECONDS = 4.0

#: Anything shorter than /8 is a default route in disguise.
MIN_PREFIX = 8

_FAILURE = "Could not apply the VPN routes."

RunIp = Callable[[list[str]], CommandResult]


def parse_routes(text: str) -> tuple[str, ...]:
    """Parse comma, space or newline separated IPv4 CIDRs into a canonical tuple."""
    found: list[str] = []
    for token in text.replace(",", " ").split():
        try:
            network = ipaddress.IPv4Network(token, strict=False)
        except ValueError as error:
            raise ConfigError(f"{token} is not a valid IPv4 route.") from error
        if network.prefixlen < MIN_PREFIX:
            raise ConfigError(f"{token} is too broad. Use /{MIN_PREFIX} or longer.")
        canonical = str(network)
        if canonical not in found:
            found.append(canonical)
    return tuple(found)


def _ip(args: list[str]) -> CommandResult:
    return run(["ip", "-4", *args], timeout=10.0)


def _tunnel_gateway(run_ip: RunIp) -> str:
    """Next hop on the tunnel: a ``via`` Kerio already uses, else the network's .1."""
    routes = run_ip(["-o", "route", "show", "dev", TUNNEL_DEVICE]).stdout.split()
    if "via" in routes:
        return routes[routes.index("via") + 1]
    # ponytail: assumes the Kerio gateway is .1 of the tunnel subnet (true for 9.6.1).
    addr = run_ip(["-o", "addr", "show", "dev", TUNNEL_DEVICE]).stdout.split()
    if "inet" not in addr:
        raise ConfigError("The VPN tunnel is not up.")
    interface = ipaddress.IPv4Interface(addr[addr.index("inet") + 1])
    return str(interface.network.network_address + 1)


def apply_split_routes(
    routes: Iterable[str],
    *,
    run_ip: RunIp = _ip,
    sleep: Callable[[float], None] = time.sleep,
    wait_seconds: float = HIJACK_WAIT_SECONDS,
) -> None:
    """Root-side: drop the hijack routes and route ``routes`` through the tunnel."""
    cidrs = parse_routes(" ".join(routes))
    gateway = _tunnel_gateway(run_ip)

    waited = 0.0
    while waited < wait_seconds and not run_ip(["route", "show", HIJACK_ROUTES[0]]).stdout.strip():
        sleep(0.5)
        waited += 0.5

    for hijack in HIJACK_ROUTES:
        run_ip(["route", "del", hijack, "dev", TUNNEL_DEVICE])  # absent is fine
    for cidr in cidrs:
        result = run_ip(["route", "replace", cidr, "via", gateway, "dev", TUNNEL_DEVICE, "onlink"])
        if not result.ok:
            raise ConfigError(f"Could not add the route {cidr}.")


def request_split_routes(routes: tuple[str, ...], *, helper: Path = HELPER_PATH) -> ActionOutcome:
    """Unprivileged side: ask the helper to apply ``routes``. Sent on stdin."""
    if not helper.is_file():
        return ActionOutcome(ok=False, message=_FAILURE)
    result = run(
        ["pkexec", str(helper), "routes"],
        timeout=CONTROL_TIMEOUT_SECONDS,
        input_text="\n".join(routes),
    )
    return outcome_from_result(
        result,
        success_message="Applied the VPN routes.",
        failure_message=_FAILURE,
    )
