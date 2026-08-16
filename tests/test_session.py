"""Tests for preferences, autostart, notifications, and tray presentation."""

from __future__ import annotations

from pathlib import Path

import pytest

from yakg.backend.autostart import (
    DESKTOP_FILENAME,
    applications_desktop_text,
    apply_start_at_login,
    autostart_desktop_text,
    autostart_enabled,
    disable_autostart,
    enable_autostart,
    ensure_application_desktop,
    ensure_application_icon,
)
from yakg.backend.notify import (
    CONNECTED_TITLE,
    DISCONNECTED_TITLE,
    TIMEOUT_TITLE,
    next_notification,
    notification_body,
)
from yakg.backend.paths import autostart_dir, config_home, data_home
from yakg.backend.preferences import load_preferences, preferences_path, save_preferences
from yakg.backend.profiles import ProfileStore, get_active_id, save_profile, set_active_id
from yakg.backend.tray_spec import (
    MENU_INTERFACE_XML,
    SNI_INTERFACE_XML,
    icon_name,
    menu_entries,
    primary_label,
    tooltip_text,
)
from yakg.main import _build_parser
from yakg.models.connection import ConnectionInfo, ConnectionState
from yakg.models.installation import Installation
from yakg.models.profile import Profile, ProfileState
from yakg.models.service import ServiceState
from yakg.models.snapshot import SystemSnapshot

FINGERPRINT = "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99"
PROFILE_ID = "11111111-1111-1111-1111-111111111111"


def _snapshot(
    state: ConnectionState,
    *,
    name: str = "Work",
    server: str = "vpn.example.com",
    username: str = "secret-user",
) -> SystemSnapshot:
    profile = Profile(
        id=PROFILE_ID,
        name=name,
        server=server,
        port=4090,
        username=username,
        fingerprint=FINGERPRINT,
    )
    return SystemSnapshot(
        installation=Installation(installed=True),
        service=ServiceState(exists=True, active=state is ConnectionState.CONNECTED),
        connection=ConnectionInfo(state=state, server=server, port=4090),
        profiles=ProfileState(profiles=(profile,), active_id=profile.id),
    )


class TestPaths:
    def test_autostart_and_data_dirs_follow_xdg(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

        assert autostart_dir() == tmp_path / "cfg" / "autostart"
        assert data_home() == tmp_path / "data" / "applications"

    def test_defaults_never_use_the_real_home_in_tests(self) -> None:
        assert autostart_dir() != Path.home() / ".config" / "autostart"
        assert data_home() != Path.home() / ".local" / "share" / "applications"


class TestPreferences:
    def test_round_trip_does_not_touch_state_json(self, tmp_path: Path) -> None:
        root = tmp_path / "yakg"
        store = ProfileStore(root)
        profile = save_profile(
            store,
            Profile(
                id=PROFILE_ID,
                name="Work",
                server="vpn.example.com",
                port=4090,
                username="someone",
                fingerprint=FINGERPRINT,
            ),
        )
        set_active_id(store, profile.id)

        from yakg.backend.preferences import Preferences

        save_preferences(Preferences(start_at_login=True, notifications_enabled=False), root)

        loaded = load_preferences(root)
        assert loaded.start_at_login
        assert not loaded.notifications_enabled
        assert get_active_id(store) == profile.id
        assert (root / "state.json").is_file()
        assert "active_id" in (root / "state.json").read_text(encoding="utf-8")
        assert "start_at_login" not in (root / "state.json").read_text(encoding="utf-8")
        path = preferences_path(root)
        assert (path.stat().st_mode & 0o777) == 0o600

    def test_defaults_when_missing(self, tmp_path: Path) -> None:
        loaded = load_preferences(tmp_path / "missing")
        assert not loaded.start_at_login
        assert loaded.notifications_enabled


class TestAutostart:
    def test_enable_writes_hidden_exec(self, tmp_path: Path) -> None:
        folder = tmp_path / "autostart"
        path = enable_autostart(folder)

        text = path.read_text(encoding="utf-8")
        assert path.name == DESKTOP_FILENAME
        assert "Exec=yakg --hidden" in text
        assert "Hidden=true" not in text
        assert autostart_enabled(folder)

    def test_disable_removes_the_file(self, tmp_path: Path) -> None:
        folder = tmp_path / "autostart"
        enable_autostart(folder)
        disable_autostart(folder)

        assert not (folder / DESKTOP_FILENAME).exists()
        assert not autostart_enabled(folder)

    def test_apply_start_at_login_uses_injected_dirs(self, tmp_path: Path) -> None:
        config = tmp_path / "yakg"
        auto = tmp_path / "autostart"
        apps = tmp_path / "applications"

        outcome = apply_start_at_login(
            True,
            config_root=config,
            autostart=auto,
            applications=apps,
        )

        assert outcome.ok
        assert load_preferences(config).start_at_login
        assert autostart_enabled(auto)
        assert (apps / DESKTOP_FILENAME).is_file()
        assert "--hidden" not in applications_desktop_text()
        assert "--hidden" in autostart_desktop_text()
        assert "Icon=io.github.yakg.Yakg" in applications_desktop_text()
        assert "Icon=io.github.yakg.Yakg" in autostart_desktop_text()

    def test_ensure_application_icon_copies_the_svg(self, tmp_path: Path) -> None:
        from yakg.resources import ICON_FILENAME, icon_path

        destination = ensure_application_icon(tmp_path / "apps")
        assert destination.name == ICON_FILENAME
        assert destination.read_bytes() == icon_path().read_bytes()

    def test_ensure_application_desktop_does_not_overwrite(self, tmp_path: Path) -> None:
        folder = tmp_path / "applications"
        path = ensure_application_desktop(folder)
        path.write_text("kept\n", encoding="utf-8")
        ensure_application_desktop(folder)
        assert path.read_text(encoding="utf-8") == "kept\n"

    def test_never_writes_the_real_autostart_dir(self) -> None:
        real = Path.home() / ".config" / "autostart" / DESKTOP_FILENAME
        before = real.exists()
        enable_autostart()
        assert autostart_dir() != Path.home() / ".config" / "autostart"
        if not before:
            assert not real.exists()


class TestNotifyPolicy:
    def test_no_event_on_unchanged_or_connecting(self) -> None:
        connected = _snapshot(ConnectionState.CONNECTED)
        connecting = _snapshot(ConnectionState.CONNECTING)

        assert (
            next_notification(connected, connected, window_visible=False, user_stop=False) is None
        )
        assert (
            next_notification(connected, connecting, window_visible=False, user_stop=False) is None
        )

    def test_first_visible_launch_is_silent(self) -> None:
        assert (
            next_notification(
                None,
                _snapshot(ConnectionState.CONNECTED),
                window_visible=True,
                user_stop=False,
            )
            is None
        )

    def test_hidden_startup_notifies_when_already_connected(self) -> None:
        event = next_notification(
            None,
            _snapshot(ConnectionState.CONNECTED),
            window_visible=False,
            user_stop=False,
        )

        assert event is not None
        assert event.title == CONNECTED_TITLE
        assert "Work" in event.body
        assert "vpn.example.com" in event.body
        assert "secret-user" not in event.body

    def test_hidden_startup_stays_silent_when_down(self) -> None:
        assert (
            next_notification(
                None,
                _snapshot(ConnectionState.DISCONNECTED),
                window_visible=False,
                user_stop=False,
            )
            is None
        )

    def test_drop_notifies_user_stop_does_not(self) -> None:
        previous = _snapshot(ConnectionState.CONNECTED)
        current = _snapshot(ConnectionState.DISCONNECTED)

        drop = next_notification(previous, current, window_visible=True, user_stop=False)
        stopped = next_notification(previous, current, window_visible=True, user_stop=True)

        assert drop is not None
        assert drop.title == DISCONNECTED_TITLE
        assert stopped is None

    def test_connected_notifies_only_when_hidden(self) -> None:
        previous = _snapshot(ConnectionState.CONNECTING)
        current = _snapshot(ConnectionState.CONNECTED)

        hidden = next_notification(previous, current, window_visible=False, user_stop=False)
        visible = next_notification(previous, current, window_visible=True, user_stop=False)

        assert hidden is not None
        assert hidden.title == CONNECTED_TITLE
        assert visible is None

    def test_timeout_notifies_only_when_hidden(self) -> None:
        current = _snapshot(ConnectionState.CONNECTING)
        hidden = next_notification(
            current,
            current,
            window_visible=False,
            user_stop=False,
            timeout=True,
        )
        visible = next_notification(
            current,
            current,
            window_visible=True,
            user_stop=False,
            timeout=True,
        )

        assert hidden is not None
        assert hidden.title == TIMEOUT_TITLE
        assert visible is None

    def test_body_never_includes_the_username(self) -> None:
        body = notification_body(_snapshot(ConnectionState.CONNECTED, username="secret-user"))
        assert "secret-user" not in body


class TestParser:
    def test_hidden_flag(self) -> None:
        args = _build_parser().parse_args(["--hidden"])
        assert args.hidden
        assert not _build_parser().parse_args([]).hidden


class TestTraySpec:
    def test_icons_and_labels_follow_state(self) -> None:
        assert icon_name(ConnectionState.CONNECTED) == "network-vpn"
        assert icon_name(ConnectionState.CONNECTING) == "network-vpn-acquiring"
        assert icon_name(ConnectionState.DISCONNECTED) == "network-offline"
        assert primary_label(ConnectionState.CONNECTED) == "Disconnect"
        assert primary_label(ConnectionState.CONNECTING) == "Cancel"
        assert primary_label(ConnectionState.DISCONNECTED, True) == "Cancel"
        assert primary_label(ConnectionState.DISCONNECTED) == "Connect"

    def test_tooltip_and_menu_omit_usernames(self) -> None:
        tip = tooltip_text(ConnectionState.CONNECTED, "vpn.example.com")
        assert "YAKG" in tip
        assert "vpn.example.com" in tip
        assert "secret-user" not in tip
        labels = " ".join(
            str(props.get("label", "")) for _id, props in menu_entries(ConnectionState.CONNECTED)
        )
        assert "Disconnect" in labels
        assert "Show" in labels
        assert "Quit" in labels

    def test_introspection_xml_is_self_contained(self) -> None:
        assert "org.kde.StatusNotifierItem" in SNI_INTERFACE_XML
        assert "com.canonical.dbusmenu" in MENU_INTERFACE_XML
        assert "RegisterStatusNotifierItem" not in SNI_INTERFACE_XML


class TestDefaultStoreIsolation:
    def test_preferences_go_to_xdg_config_home(self) -> None:
        from yakg.backend.preferences import Preferences

        save_preferences(Preferences(start_at_login=True))
        assert preferences_path().is_relative_to(config_home())
        assert Path.home() / ".config" / "yakg" / "preferences.json" != preferences_path()
