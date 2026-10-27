"""Split-tunnel route parsing and the root-side apply, with ``ip`` faked."""

from __future__ import annotations

import pytest

from yakg.backend.config import ConfigError
from yakg.backend.process import CommandResult
from yakg.backend.routes import apply_split_routes, parse_routes


class TestParseRoutes:
    def test_accepts_commas_spaces_and_dedupes(self) -> None:
        assert parse_routes("192.168.70.0/24, 10.0.0.5/32 192.168.70.0/24") == (
            "192.168.70.0/24",
            "10.0.0.5/32",
        )

    def test_empty_is_no_routes(self) -> None:
        assert parse_routes("") == ()

    @pytest.mark.parametrize("bad", ["0.0.0.0/0", "0.0.0.0/1", "128.0.0.0/4", "nope", "1.2.3/24"])
    def test_rejects_default_routes_and_garbage(self, bad: str) -> None:
        with pytest.raises(ConfigError):
            parse_routes(bad)


class FakeIp:
    def __init__(self, *, hijack: bool) -> None:
        self.calls: list[list[str]] = []
        self.hijack = hijack

    def __call__(self, args: list[str]) -> CommandResult:
        self.calls.append(args)
        out = ""
        if args[:3] == ["-o", "route", "show"]:
            out = "192.168.33.0/24 via 10.189.125.1 dev kvnet onlink"
        elif args[:2] == ["route", "show"] and self.hijack:
            out = "0.0.0.0/1 via 10.189.125.1 dev kvnet"
        return CommandResult(argv=tuple(args), returncode=0, stdout=out)


def test_removes_hijack_and_adds_routes() -> None:
    ip = FakeIp(hijack=True)
    apply_split_routes(["192.168.70.0/24"], run_ip=ip, sleep=lambda _s: None)
    assert ["route", "del", "0.0.0.0/1", "dev", "kvnet"] in ip.calls
    assert ["route", "del", "128.0.0.0/1", "dev", "kvnet"] in ip.calls
    assert [
        "route",
        "replace",
        "192.168.70.0/24",
        "via",
        "10.189.125.1",
        "dev",
        "kvnet",
        "onlink",
    ] in ip.calls


def test_gives_up_waiting_when_server_pushes_no_hijack() -> None:
    sleeps: list[float] = []
    ip = FakeIp(hijack=False)
    apply_split_routes(["192.168.70.0/24"], run_ip=ip, sleep=sleeps.append, wait_seconds=1.0)
    assert sum(sleeps) == 1.0
