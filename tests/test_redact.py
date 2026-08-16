"""Tests for secret redaction.

The Kerio configuration carries a recoverable password, so these cases are the
guard against it reaching a log file.
"""

from __future__ import annotations

import logging

from yakg.utils.logging import RedactingFilter
from yakg.utils.redact import PLACEHOLDER, redact


def test_xor_password_is_removed() -> None:
    # The form the Kerio maintainer script writes into /etc/kerio-kvc.conf.
    assert redact("<password>XOR:3a3b3c</password>") == f"<password>{PLACEHOLDER}</password>"


def test_bare_xor_value_is_removed() -> None:
    assert "3a3b3c" not in redact("stored as XOR:3a3b3c in the file")


def test_password_element_is_removed_whatever_it_contains() -> None:
    redacted = redact("<password>hunter2</password>")

    assert "hunter2" not in redacted
    assert redacted == f"<password>{PLACEHOLDER}</password>"


def test_fingerprint_element_is_removed() -> None:
    assert "AA:BB" not in redact("<fingerprint>AA:BB:CC</fingerprint>")


def test_key_value_forms_are_removed() -> None:
    for text in (
        "password=hunter2",
        "password: hunter2",
        'password="hunter2"',
        "PASSWORD = hunter2",
        "api_key: hunter2",
        "private-key = hunter2",
    ):
        assert "hunter2" not in redact(text), text


def test_pem_private_key_is_removed() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow==\n-----END RSA PRIVATE KEY-----"

    assert redact(f"key: {pem}") == f"key: {PLACEHOLDER}"


def test_ordinary_text_is_untouched() -> None:
    text = "Service kerio-kvc.service is active and enabled at boot"

    assert redact(text) == text


def test_whole_config_file_is_scrubbed() -> None:
    config = (
        "<config><connections><connection type='persistent'>"
        "<server>vpn.example.com</server><port>4090</port>"
        "<username>someone</username><password>XOR:3a3b3c</password>"
        "<fingerprint>AA:BB:CC</fingerprint><active>1</active>"
        "</connection></connections></config>"
    )

    redacted = redact(config)

    assert "3a3b3c" not in redacted
    assert "AA:BB:CC" not in redacted
    # Non-secret fields survive, because they are what makes a log useful.
    assert "vpn.example.com" in redacted
    assert "someone" in redacted


def test_filter_redacts_deferred_log_arguments() -> None:
    # A secret passed as a %s argument must not slip past the filter.
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="config says %s",
        args=("<password>hunter2</password>",),
        exc_info=None,
    )

    assert RedactingFilter().filter(record) is True
    assert "hunter2" not in record.getMessage()
    assert record.args is None


def test_filter_survives_a_broken_format_string() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="missing arg %s %s",
        args=("only one",),
        exc_info=None,
    )

    assert RedactingFilter().filter(record) is True
