"""Tests for profile files, activate, and first-run import."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config_fixtures import EXAMPLE_FINGERPRINT, POSTINST_XML
from conftest import InstallRunner, result
from yakg.backend.activate import (
    MISSING_PASSWORD_MESSAGE,
    activate_profile,
    import_from_kerio,
    maybe_import_first_profile,
    profile_to_config,
    resolve_profile_password,
)
from yakg.backend.config import ConfigError, generate_config, parse_config, xor_decode, xor_encode
from yakg.backend.config_access import ConfigReadResult
from yakg.backend.paths import config_home
from yakg.backend.profiles import (
    ProfileStore,
    default_store,
    delete_profile,
    get_active_id,
    get_profile,
    list_profiles,
    new_profile_id,
    save_profile,
    set_active_id,
    validate_profile_name,
)
from yakg.backend.secrets import NO_KEYRING_MESSAGE, LibsecretStore, MemorySecretStore
from yakg.backend.service import NullBackend, ServiceManager
from yakg.models.action import ActionOutcome
from yakg.models.installation import Installation
from yakg.models.profile import Profile
from yakg.models.service import ServiceBackendKind, ServiceState

LIVE_CONFIG = Path("/etc/kerio-kvc.conf")
HELPER = "/usr/libexec/yakg-helper"


def _profile(store: ProfileStore, **overrides: object) -> Profile:
    values: dict[str, object] = {
        "id": new_profile_id(),
        "name": "Work",
        "server": "vpn.example.com",
        "port": 4090,
        "username": "someone",
        "fingerprint": EXAMPLE_FINGERPRINT,
    }
    values.update(overrides)
    return save_profile(store, Profile(**values))  # type: ignore[arg-type]


class TestPaths:
    def test_uses_xdg_config_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

        assert config_home() == tmp_path / "xdg" / "yakg"

    def test_home_argument_never_touches_the_real_home(self, tmp_path: Path) -> None:
        assert config_home(environ={}, home=tmp_path) == tmp_path / ".config" / "yakg"

    def test_default_store_is_isolated_from_the_real_home(self) -> None:
        store = default_store()

        assert store.root == config_home()
        assert store.root != Path.home() / ".config" / "yakg"


class TestProfileStore:
    def test_round_trip_and_mode(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "yakg")
        saved = _profile(store)

        loaded = get_profile(store, saved.id)
        assert loaded == saved
        path = store.profiles_dir / f"{saved.id}.json"
        assert (path.stat().st_mode & 0o777) == 0o600
        assert (store.profiles_dir.stat().st_mode & 0o777) == 0o700
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "password" not in payload
        assert "password_xor" not in payload

    def test_rejects_a_path_as_a_name(self) -> None:
        with pytest.raises(ConfigError, match="not valid"):
            validate_profile_name("../etc/passwd")

    def test_strips_planted_secrets_and_does_not_write_them_back(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "yakg")
        saved = _profile(store)
        path = store.profiles_dir / f"{saved.id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["password"] = "s3cret"
        payload["password_xor"] = "XOR:aabbcc"
        path.write_text(json.dumps(payload), encoding="utf-8")

        loaded = get_profile(store, saved.id)
        assert loaded is not None
        save_profile(store, loaded)
        text = path.read_text(encoding="utf-8")
        rewritten = json.loads(text)

        assert "password" not in rewritten
        assert "password_xor" not in rewritten
        assert "s3cret" not in text
        assert "XOR:" not in text

    def test_strips_a_planted_xor_key(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "yakg")
        saved = _profile(store)
        path = store.profiles_dir / f"{saved.id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["XOR:"] = "deadbeef"
        path.write_text(json.dumps(payload), encoding="utf-8")

        loaded = get_profile(store, saved.id)
        assert loaded is not None
        save_profile(store, loaded)
        text = path.read_text(encoding="utf-8")

        assert "XOR:" not in text
        assert "deadbeef" not in text

    def test_delete_clears_active_id(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "yakg")
        saved = _profile(store)
        set_active_id(store, saved.id)

        delete_profile(store, saved.id)

        assert get_profile(store, saved.id) is None
        assert get_active_id(store) is None
        assert list_profiles(store) == ()

    def test_never_writes_under_etc(self, tmp_path: Path) -> None:
        live_before = LIVE_CONFIG.stat().st_mtime_ns if LIVE_CONFIG.exists() else None
        store = ProfileStore(tmp_path / "yakg")
        _profile(store)
        live_after = LIVE_CONFIG.stat().st_mtime_ns if LIVE_CONFIG.exists() else None

        assert live_before == live_after
        assert not str(store.root).startswith("/etc/")

    def test_repr_omits_the_username(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "yakg")
        saved = _profile(store, username="someone")

        assert "someone" not in repr(saved)


class TestActivate:
    def test_writes_xor_xml_on_stdin_never_argv(
        self, fake_run: InstallRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "yakg.backend.config_access.helper_available",
            lambda _helper=None: True,
        )
        runner = fake_run("yakg.backend.config_access", {f"{HELPER} write": result()})
        store = ProfileStore(tmp_path / "yakg")
        profile = _profile(store)

        outcome = activate_profile(
            profile,
            "s3cret",
            store=store,
            manager=ServiceManager(NullBackend()),
        )

        assert outcome.ok
        assert runner.calls[-1] == ("pkexec", HELPER, "write")
        xml = str(runner.kwargs_list[-1]["input_text"])
        assert "s3cret" not in xml
        assert xor_encode("s3cret") in xml
        assert get_active_id(store) == profile.id

    def test_empty_password_does_not_write(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "yakg")
        profile = _profile(store)
        called = False

        def write(_xml: str) -> ActionOutcome:
            nonlocal called
            called = True
            return ActionOutcome(ok=True)

        outcome = activate_profile(
            profile,
            "",
            store=store,
            write=write,
            manager=ServiceManager(NullBackend()),
        )

        assert not outcome.ok
        assert outcome.message == MISSING_PASSWORD_MESSAGE
        assert not called
        assert get_active_id(store) is None

    def test_failed_write_does_not_set_active_id(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "yakg")
        profile = _profile(store)

        outcome = activate_profile(
            profile,
            "s3cret",
            store=store,
            write=lambda _xml: ActionOutcome(ok=False, message="denied"),
            manager=ServiceManager(NullBackend()),
        )

        assert not outcome.ok
        assert get_active_id(store) is None

    def test_reloads_only_when_the_service_is_running(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "yakg")
        profile = _profile(store)
        manager = _RunningManager()

        outcome = activate_profile(
            profile,
            "s3cret",
            store=store,
            write=lambda _xml: ActionOutcome(ok=True),
            manager=manager,
        )

        assert outcome.ok
        assert manager.reload_calls == 1

    def test_generated_config_has_no_plaintext(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "yakg")
        profile = _profile(store)
        xml = generate_config(profile_to_config(profile, "s3cret"))

        assert "s3cret" not in xml
        assert xor_encode("s3cret") in xml


class TestImport:
    def test_seeds_the_secret_store_without_writing_xor(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "yakg")
        secrets = MemorySecretStore()
        config = parse_config(POSTINST_XML)

        profile = import_from_kerio(config, store=store, secrets=secrets)
        path = store.profiles_dir / f"{profile.id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))

        assert payload["server"] == "vpn.example.com"
        assert "password" not in payload
        assert "XOR:" not in json.dumps(payload)
        assert secrets.get(profile.id) == xor_decode(config.password_xor)
        assert get_active_id(store) == profile.id

    def test_maybe_import_is_a_no_op_when_profiles_exist(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "yakg")
        _profile(store)
        called = False

        def read() -> ConfigReadResult:
            nonlocal called
            called = True
            return ConfigReadResult(None, ActionOutcome(ok=True), missing=True)

        outcome = maybe_import_first_profile(
            Installation(installed=True, config_present=True),
            store=store,
            secrets=MemorySecretStore(),
            read=read,
        )

        assert outcome.ok
        assert not called

    def test_maybe_import_stays_silent_when_cancelled(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "yakg")

        def read() -> ConfigReadResult:
            return ConfigReadResult(
                None,
                ActionOutcome(ok=False, cancelled=True, message=""),
            )

        outcome = maybe_import_first_profile(
            Installation(installed=True, config_present=True),
            store=store,
            secrets=MemorySecretStore(),
            read=read,
        )

        assert outcome.cancelled
        assert list_profiles(store) == ()


class TestMemorySecretStore:
    def test_round_trip(self) -> None:
        store = MemorySecretStore()
        assert store.set("abc", "pw").ok
        assert store.contains("abc")
        assert store.get("abc") == "pw"
        assert store.delete("abc").ok
        assert store.get("abc") is None


class TestSecretFailures:
    def test_missing_password_is_classified(self) -> None:
        password, outcome = resolve_profile_password(MemorySecretStore(), "missing")

        assert password == ""
        assert not outcome.ok
        assert outcome.message == MISSING_PASSWORD_MESSAGE

    def test_unreachable_store_is_classified(self) -> None:
        password, outcome = resolve_profile_password(_DownSecretStore(), "any")

        assert password == ""
        assert not outcome.ok
        assert outcome.message == NO_KEYRING_MESSAGE

    def test_libsecret_without_gi_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("yakg.backend.secrets._secret_module", lambda: None)
        store = LibsecretStore()

        assert store.get("abc") is None
        assert not store.reachable()
        assert not store.set("abc", "pw").ok
        assert store.set("abc", "pw").message == NO_KEYRING_MESSAGE

    def test_secrets_module_does_not_import_gi_at_load(self) -> None:
        from yakg.backend import secrets as secrets_module

        text = Path(secrets_module.__file__).read_text(encoding="utf-8")
        before_def = text.split("def _secret_module")[0]
        assert "from gi.repository import Secret" not in before_def
        assert "import gi" not in before_def


class TestUiDoesNotDecode:
    def test_settings_and_window_never_call_xor_decode(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "yakg" / "ui"
        for name in ("settings.py", "window.py"):
            text = (root / name).read_text(encoding="utf-8")
            assert "xor_decode(" not in text


class _DownSecretStore(MemorySecretStore):
    def reachable(self) -> bool:
        return False


class _RunningManager(ServiceManager):
    def __init__(self) -> None:
        super().__init__(NullBackend())
        self.reload_calls = 0

    def get_service_status(self) -> ServiceState:
        return ServiceState(exists=True, backend=ServiceBackendKind.SYSTEMD, active=True)

    def reload_service(self) -> ActionOutcome:
        self.reload_calls += 1
        return ActionOutcome(ok=True)
