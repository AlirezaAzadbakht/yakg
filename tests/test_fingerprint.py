"""Tests for openssl-based fingerprint detection."""

from __future__ import annotations

from config_fixtures import EXAMPLE_FINGERPRINT
from conftest import InstallRunner, result
from yakg.backend.fingerprint import (
    FINGERPRINT_TIMEOUT_SECONDS,
    connect_argument,
    detect_fingerprint,
    extract_certificate_pem,
    parse_openssl_fingerprint,
)

MODULE = "yakg.backend.fingerprint"

_PEM = """\
-----BEGIN CERTIFICATE-----
MIIBozCCAQwCCQDfake0nlyForTests==
-----END CERTIFICATE-----
"""


class TestConnectArgument:
    def test_hostname(self) -> None:
        assert connect_argument("vpn.example.com", 4090) == "vpn.example.com:4090"

    def test_ipv6_is_bracketed(self) -> None:
        assert connect_argument("2001:db8::1", 4090) == "[2001:db8::1]:4090"
        assert connect_argument("[2001:db8::1]", 4090) == "[2001:db8::1]:4090"


class TestParseOpenssl:
    def test_extracts_the_digest(self) -> None:
        assert (
            parse_openssl_fingerprint(f"MD5 Fingerprint={EXAMPLE_FINGERPRINT}\n")
            == EXAMPLE_FINGERPRINT
        )

    def test_extracts_pem(self) -> None:
        wrapped = f"CONNECTED\n{_PEM}\nVerify return code: 0\n"
        assert _PEM.strip() in extract_certificate_pem(wrapped)


class TestDetectFingerprint:
    def test_two_step_openssl_with_validated_host(self, fake_run: InstallRunner) -> None:
        runner = fake_run(
            MODULE,
            {
                "s_client": result(stdout=f"CONNECTED\n{_PEM}\n"),
                "x509": result(stdout=f"MD5 Fingerprint={EXAMPLE_FINGERPRINT}\n"),
            },
        )

        fingerprint, outcome = detect_fingerprint("vpn.example.com", 4090)

        assert outcome.ok
        assert fingerprint == EXAMPLE_FINGERPRINT
        assert runner.calls[0] == (
            "openssl",
            "s_client",
            "-connect",
            "vpn.example.com:4090",
            "-servername",
            "vpn.example.com",
        )
        assert runner.calls[1] == ("openssl", "x509", "-fingerprint", "-md5", "-noout")
        assert runner.kwargs_list[0]["timeout"] == FINGERPRINT_TIMEOUT_SECONDS
        assert _PEM.strip() in str(runner.kwargs_list[1]["input_text"])

    def test_rejects_a_host_with_metacharacters(self, fake_run: InstallRunner) -> None:
        runner = fake_run(MODULE, {})

        fingerprint, outcome = detect_fingerprint("vpn.example.com;reboot", 4090)

        assert not outcome.ok
        assert fingerprint == ""
        assert runner.calls == []

    def test_rejects_a_bad_port(self, fake_run: InstallRunner) -> None:
        runner = fake_run(MODULE, {})

        _fingerprint, outcome = detect_fingerprint("vpn.example.com", 0)

        assert not outcome.ok
        assert runner.calls == []

    def test_missing_certificate_is_a_failure(self, fake_run: InstallRunner) -> None:
        fake_run(MODULE, {"s_client": result(stdout="CONNECTED\n")})

        fingerprint, outcome = detect_fingerprint("vpn.example.com", 4090)

        assert not outcome.ok
        assert fingerprint == ""
        assert "certificate" in outcome.message.lower()
