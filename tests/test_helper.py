"""Tests for the privileged helper verbs.

Paths are injected. These tests never open ``/etc/kerio-kvc.conf``.
"""

from __future__ import annotations

import io
from pathlib import Path

from config_fixtures import POSTINST_XML
from yakg.backend.config import parse_config
from yakg.helper import EXIT_ERROR, EXIT_MISSING, EXIT_OK, EXIT_USAGE, dispatch


def _run(
    args: list[str],
    *,
    config_path: Path,
    backup_dir: Path,
    stdin: str = "",
) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    code = dispatch(
        args,
        stdin=io.StringIO(stdin),
        stdout=out,
        stderr=err,
        config_path=config_path,
        backup_dir=backup_dir,
    )
    return code, out.getvalue(), err.getvalue()


class TestHelperRead:
    def test_prints_the_file(self, tmp_path: Path) -> None:
        config_path = tmp_path / "kerio-kvc.conf"
        config_path.write_text(POSTINST_XML, encoding="utf-8")

        code, stdout, _stderr = _run(
            ["read"],
            config_path=config_path,
            backup_dir=tmp_path / "backups",
        )

        assert code == EXIT_OK
        assert parse_config(stdout).server == "vpn.example.com"

    def test_missing_file_is_a_distinct_status(self, tmp_path: Path) -> None:
        code, _stdout, stderr = _run(
            ["read"],
            config_path=tmp_path / "absent",
            backup_dir=tmp_path / "backups",
        )

        assert code == EXIT_MISSING
        assert "missing" in stderr.lower()


class TestHelperWrite:
    def test_writes_from_stdin_not_argv(self, tmp_path: Path) -> None:
        config_path = tmp_path / "kerio-kvc.conf"
        backup_dir = tmp_path / "backups"

        code, _stdout, _stderr = _run(
            ["write"],
            config_path=config_path,
            backup_dir=backup_dir,
            stdin=POSTINST_XML,
        )

        assert code == EXIT_OK
        assert parse_config(config_path.read_text(encoding="utf-8")).server == "vpn.example.com"

    def test_rejects_empty_stdin(self, tmp_path: Path) -> None:
        code, _stdout, stderr = _run(
            ["write"],
            config_path=tmp_path / "kerio-kvc.conf",
            backup_dir=tmp_path / "backups",
            stdin="",
        )

        assert code == EXIT_ERROR
        assert "empty" in stderr.lower()

    def test_rejects_a_path_argument(self, tmp_path: Path) -> None:
        code, _stdout, _stderr = _run(
            ["write", "/etc/kerio-kvc.conf"],
            config_path=tmp_path / "kerio-kvc.conf",
            backup_dir=tmp_path / "backups",
            stdin=POSTINST_XML,
        )

        assert code == EXIT_USAGE


class TestHelperRestore:
    def test_rejects_parent_traversal(self, tmp_path: Path) -> None:
        code, _stdout, stderr = _run(
            ["restore", "../etc/passwd"],
            config_path=tmp_path / "kerio-kvc.conf",
            backup_dir=tmp_path / "backups",
        )

        assert code == EXIT_ERROR
        assert "not allowed" in stderr

    def test_rejects_an_absolute_path(self, tmp_path: Path) -> None:
        code, _stdout, stderr = _run(
            ["restore", "/etc/passwd"],
            config_path=tmp_path / "kerio-kvc.conf",
            backup_dir=tmp_path / "backups",
        )

        assert code == EXIT_ERROR
        assert "not allowed" in stderr

    def test_restores_a_named_backup(self, tmp_path: Path) -> None:
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        backup = backup_dir / "kerio-kvc.conf.20260816T145430Z"
        backup.write_text(POSTINST_XML, encoding="utf-8")
        config_path = tmp_path / "kerio-kvc.conf"
        config_path.write_text("stale", encoding="utf-8")

        code, _stdout, _stderr = _run(
            ["restore", backup.name],
            config_path=config_path,
            backup_dir=backup_dir,
        )

        assert code == EXIT_OK
        assert parse_config(config_path.read_text(encoding="utf-8")).server == "vpn.example.com"


class TestHelperDoesNotImportGtk:
    def test_source_does_not_mention_gi(self) -> None:
        import yakg.backend.config as config_module
        import yakg.helper as helper_module

        assert "gi" not in Path(helper_module.__file__).read_text(encoding="utf-8")
        assert "gi.repository" not in Path(config_module.__file__).read_text(encoding="utf-8")
