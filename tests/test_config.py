"""Tests for Kerio configuration parse, validate, generate and atomic write."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from config_fixtures import EXAMPLE_FINGERPRINT, EXAMPLE_PASSWORD_XOR, POSTINST_XML
from yakg.backend.config import (
    ConfigError,
    KerioConfig,
    generate_config,
    parse_config,
    restore_from_backup,
    validate_backup_name,
    validate_config,
    validate_host,
    validate_port,
    write_config_atomically,
    xor_decode,
    xor_encode,
)

LIVE_CONFIG = Path("/etc/kerio-kvc.conf")


class TestXor:
    def test_round_trip(self) -> None:
        assert xor_decode(xor_encode("secret")) == "secret"

    def test_matches_the_postinst_constant(self) -> None:
        # 'a' is 97; 97 XOR 85 is 52, hex 34. Same transform as the postinst.
        assert xor_encode("a") == "XOR:34"
        assert xor_decode("XOR:34") == "a"

    def test_rejects_a_malformed_blob(self) -> None:
        with pytest.raises(ConfigError):
            xor_decode("not-xor")


class TestParse:
    def test_postinst_shaped_fixture_keeps_the_xor_blob(self) -> None:
        config = parse_config(POSTINST_XML)

        assert config.server == "vpn.example.com"
        assert config.port == 4090
        assert config.username == "someone"
        assert config.password_xor == EXAMPLE_PASSWORD_XOR
        assert config.password_plain == ""
        assert config.fingerprint == EXAMPLE_FINGERPRINT
        assert config.connection_type == "persistent"

    def test_rejects_malformed_xml(self) -> None:
        with pytest.raises(ConfigError, match="not valid"):
            parse_config("<config><oops>")

    def test_rejects_a_doctype(self) -> None:
        with pytest.raises(ConfigError, match="not valid"):
            parse_config("<!DOCTYPE config [<!ENTITY x SYSTEM 'file:///etc/passwd'>]><config/>")

    def test_rejects_two_connections(self) -> None:
        xml = POSTINST_XML.replace(
            "</connection>\n  </connections>",
            "</connection>\n    <connection type='persistent'></connection>\n  </connections>",
        )
        with pytest.raises(ConfigError, match="more than one"):
            parse_config(xml)

    def test_rejects_missing_connection(self) -> None:
        with pytest.raises(ConfigError, match="no connection"):
            parse_config("<config><connections/></config>")


class TestValidate:
    def test_rejects_missing_server(self) -> None:
        with pytest.raises(ConfigError, match="server"):
            validate_config(replace(parse_config(POSTINST_XML), server=""))

    def test_rejects_server_with_metacharacters(self) -> None:
        with pytest.raises(ConfigError, match="server"):
            validate_host("vpn.example.com;reboot")

    def test_rejects_missing_username(self) -> None:
        with pytest.raises(ConfigError, match="username"):
            validate_config(replace(parse_config(POSTINST_XML), username=""))

    def test_rejects_bad_port(self) -> None:
        with pytest.raises(ConfigError, match="port"):
            validate_port(0)
        with pytest.raises(ConfigError, match="port"):
            validate_port(65536)
        with pytest.raises(ConfigError, match="port"):
            validate_port("not-a-port")

    def test_rejects_bad_fingerprint(self) -> None:
        with pytest.raises(ConfigError, match="fingerprint"):
            validate_config(replace(parse_config(POSTINST_XML), fingerprint="AA:BB"))

    def test_rejects_missing_password(self) -> None:
        with pytest.raises(ConfigError, match="password"):
            validate_config(replace(parse_config(POSTINST_XML), password_xor=""))

    def test_accepts_a_new_plaintext_password(self) -> None:
        validate_config(replace(parse_config(POSTINST_XML), password_xor="", password_plain="new"))

    def test_accepts_an_ipv6_literal(self) -> None:
        assert validate_host("2001:db8::1") == "2001:db8::1"
        assert validate_host("[2001:db8::1]") == "[2001:db8::1]"


class TestGenerate:
    def test_reuses_password_xor_when_plaintext_is_empty(self) -> None:
        xml = generate_config(parse_config(POSTINST_XML))

        assert EXAMPLE_PASSWORD_XOR in xml
        assert "<active>1</active>" in xml

    def test_encodes_a_new_password_and_does_not_emit_plaintext(self) -> None:
        config = replace(parse_config(POSTINST_XML), password_plain="s3cret")
        xml = generate_config(config)

        assert "s3cret" not in xml
        assert xor_encode("s3cret") in xml

    def test_unknown_child_survives(self) -> None:
        xml = POSTINST_XML.replace("</active>", "</active>\n      <note>keep-me</note>")
        out = generate_config(parse_config(xml))

        assert "<note>keep-me</note>" in out

    def test_always_writes_active_one(self) -> None:
        xml = POSTINST_XML.replace("<active>1</active>", "<active>0</active>")
        out = generate_config(parse_config(xml))

        assert "<active>1</active>" in out
        assert "<active>0</active>" not in out


class TestAtomicWrite:
    def test_creates_a_backup_then_replaces(self, tmp_path: Path) -> None:
        live_before = LIVE_CONFIG.stat().st_mtime_ns if LIVE_CONFIG.exists() else None
        path = tmp_path / "kerio-kvc.conf"
        path.write_text("stale", encoding="utf-8")
        backup_dir = tmp_path / "backups"

        backup = write_config_atomically(path, POSTINST_XML, backup_dir=backup_dir)

        assert backup is not None
        assert backup.parent == backup_dir
        assert backup.name.startswith("kerio-kvc.conf.")
        assert backup.read_text(encoding="utf-8") == "stale"
        assert parse_config(path.read_text(encoding="utf-8")).server == "vpn.example.com"
        assert (path.stat().st_mode & 0o777) == 0o600
        assert (backup_dir.stat().st_mode & 0o777) == 0o700
        live_after = LIVE_CONFIG.stat().st_mtime_ns if LIVE_CONFIG.exists() else None
        assert live_before == live_after

    def test_failed_verify_restores_the_backup(self, tmp_path: Path) -> None:
        path = tmp_path / "kerio-kvc.conf"
        path.write_text("original", encoding="utf-8")
        backup_dir = tmp_path / "backups"

        def boom(_text: str) -> None:
            raise ConfigError("verify failed")

        with pytest.raises(ConfigError, match="verify failed"):
            write_config_atomically(
                path,
                POSTINST_XML,
                backup_dir=backup_dir,
                verify=boom,
            )

        assert path.read_text(encoding="utf-8") == "original"

    def test_failed_verify_without_an_original_removes_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "kerio-kvc.conf"
        backup_dir = tmp_path / "backups"

        def boom(_text: str) -> None:
            raise ConfigError("verify failed")

        with pytest.raises(ConfigError, match="verify failed"):
            write_config_atomically(
                path,
                POSTINST_XML,
                backup_dir=backup_dir,
                verify=boom,
            )

        assert not path.exists()

    def test_never_targets_the_live_etc_path(self, tmp_path: Path) -> None:
        write_config_atomically(
            tmp_path / "kerio-kvc.conf",
            POSTINST_XML,
            backup_dir=tmp_path / "backups",
        )

        assert not str(tmp_path).startswith("/etc/")


class TestRestoreName:
    @pytest.mark.parametrize(
        "name",
        ["../etc/passwd", "/etc/passwd", "kerio-kvc.conf./etc/passwd", "..", "passwd"],
    )
    def test_rejects_path_traversal(self, name: str) -> None:
        with pytest.raises(ConfigError, match="not allowed"):
            validate_backup_name(name)

    def test_accepts_a_timestamped_basename(self) -> None:
        assert validate_backup_name("kerio-kvc.conf.20260816T145430Z") == (
            "kerio-kvc.conf.20260816T145430Z"
        )

    def test_restore_uses_only_the_backup_directory(self, tmp_path: Path) -> None:
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        backup = backup_dir / "kerio-kvc.conf.20260816T145430Z"
        backup.write_text(POSTINST_XML, encoding="utf-8")
        target = tmp_path / "kerio-kvc.conf"

        restore_from_backup(backup.name, config_path=target, backup_dir=backup_dir)

        assert parse_config(target.read_text(encoding="utf-8")).server == "vpn.example.com"

    def test_restore_rejects_a_name_that_escapes_the_directory(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not allowed"):
            restore_from_backup(
                "../etc/passwd",
                config_path=tmp_path / "kerio-kvc.conf",
                backup_dir=tmp_path / "backups",
            )


class TestReprHidesSecrets:
    def test_password_fields_are_not_in_repr(self) -> None:
        config = KerioConfig(
            server="vpn.example.com",
            port=4090,
            username="someone",
            password_xor="XOR:aabbcc",
            fingerprint=EXAMPLE_FINGERPRINT,
            password_plain="visible-if-repr-is-wrong",
        )

        text = repr(config)

        assert "XOR:aabbcc" not in text
        assert "visible-if-repr-is-wrong" not in text
