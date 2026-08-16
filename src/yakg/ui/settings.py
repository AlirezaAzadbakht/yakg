"""Settings dialog for the Kerio VPN configuration.

Load, save and fingerprint detection run on worker threads. This module
never executes a system command itself.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, GLib, Gtk

from yakg.backend.config import ConfigError, KerioConfig, generate_config, validate_port
from yakg.backend.config_access import ConfigReadResult, read_config, write_config
from yakg.backend.constants import DEFAULT_PORT
from yakg.backend.fingerprint import detect_fingerprint
from yakg.backend.service import ServiceManager
from yakg.models.action import ActionOutcome
from yakg.utils.logging import get_logger

log = get_logger(__name__)

_DONE: bool = GLib.SOURCE_REMOVE

_EMPTY = KerioConfig(
    server="",
    port=DEFAULT_PORT,
    username="",
    password_xor="",
    fingerprint="",
)


class SettingsWindow(Adw.Window):
    """Modal form for the single Kerio connection."""

    def __init__(
        self,
        parent: Gtk.Window,
        *,
        on_saved: Callable[[str], None],
    ) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.set_title("Settings")
        self.set_default_size(480, 560)

        self._on_saved = on_saved
        self._loaded: KerioConfig | None = None
        self._busy = False

        self._toast_overlay = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._build_header())
        toolbar.set_content(self._toast_overlay)
        self.set_content(toolbar)

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._stack.add_named(self._build_loading_page(), "loading")
        self._stack.add_named(self._build_form(), "form")
        self._toast_overlay.set_child(self._stack)
        self._stack.set_visible_child_name("loading")

        self._load()

    def do_close_request(self) -> bool:
        result: bool = bool(Adw.Window.do_close_request(self))
        return result

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _button: self.close())
        header.pack_start(cancel)

        self._save_button = Gtk.Button(label="Save")
        self._save_button.add_css_class("suggested-action")
        self._save_button.connect("clicked", lambda _button: self._save())
        header.pack_end(self._save_button)

        return header

    def _build_loading_page(self) -> Gtk.Widget:
        spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32)
        page = Adw.StatusPage(title="Loading configuration…")
        page.set_child(spinner)
        return page

    def _build_form(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(title="VPN connection")

        self._server = Adw.EntryRow(title="Server")
        self._port = Adw.EntryRow(title="Port")
        self._port.set_text(str(DEFAULT_PORT))
        self._username = Adw.EntryRow(title="Username")
        self._fingerprint = Adw.EntryRow(title="Fingerprint")

        self._password = Gtk.PasswordEntry(
            hexpand=True,
            show_peek_icon=True,
            placeholder_text="Leave unchanged",
        )
        self._password.set_valign(Gtk.Align.CENTER)
        password_row = Adw.ActionRow(title="Password")
        password_row.add_suffix(self._password)

        self._detect_row = Adw.ActionRow(title="Detect fingerprint", activatable=True)
        self._detect_row.add_prefix(Gtk.Image(icon_name="find-location-symbolic"))
        self._detect_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self._detect_row.connect("activated", lambda _row: self._detect())

        for row in (
            self._server,
            self._port,
            self._username,
            password_row,
            self._fingerprint,
            self._detect_row,
        ):
            group.add(row)

        page = Adw.PreferencesPage()
        page.add(group)
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
        )
        scroller.set_child(page)
        return scroller

    def _load(self) -> None:
        self._busy = True
        self._set_controls_sensitive(False)
        thread = threading.Thread(target=self._load_worker, name="yakg-config-read", daemon=True)
        thread.start()

    def _load_worker(self) -> None:
        try:
            result = read_config()
        except Exception as error:
            log.exception("Reading the configuration failed")
            result = ConfigReadResult(
                None,
                ActionOutcome(
                    ok=False,
                    message="Could not read the configuration.",
                    detail=str(error),
                ),
            )
        GLib.idle_add(self._on_load_done, result)

    def _on_load_done(self, result: ConfigReadResult) -> bool:
        self._busy = False

        if result.outcome.cancelled:
            self.close()
            return _DONE

        if not result.outcome.ok and not result.missing:
            self._show_toast(result.outcome.message)
            self._apply_config(_EMPTY)
        elif result.missing or result.config is None:
            self._apply_config(_EMPTY)
        else:
            self._apply_config(result.config)

        self._stack.set_visible_child_name("form")
        self._set_controls_sensitive(True)
        return _DONE

    def _apply_config(self, config: KerioConfig) -> None:
        self._loaded = None if config is _EMPTY else config
        self._server.set_text(config.server)
        self._port.set_text(str(config.port or DEFAULT_PORT))
        self._username.set_text(config.username)
        self._fingerprint.set_text(config.fingerprint)
        # Never pre-fill the password. An empty field means leave the XOR blob.
        self._password.set_text("")

    def _save(self) -> None:
        if self._busy:
            return

        try:
            config = self._config_from_form()
            xml = generate_config(config)
        except ConfigError as error:
            self._show_toast(str(error))
            return
        except ValueError as error:
            self._show_toast(str(error))
            return

        self._busy = True
        self._set_controls_sensitive(False)
        thread = threading.Thread(
            target=self._save_worker,
            args=(xml,),
            name="yakg-config-write",
            daemon=True,
        )
        thread.start()

    def _config_from_form(self) -> KerioConfig:
        port = validate_port(self._port.get_text())
        base = self._loaded if self._loaded is not None else _EMPTY
        return replace(
            base,
            server=self._server.get_text().strip(),
            port=port,
            username=self._username.get_text().strip(),
            password_plain=self._password.get_text(),
            fingerprint=self._fingerprint.get_text().strip(),
        )

    def _save_worker(self, xml: str) -> None:
        try:
            outcome = write_config(xml)
            if outcome.ok:
                outcome = _reload_if_running(outcome)
        except Exception as error:
            log.exception("Saving the configuration failed")
            outcome = ActionOutcome(
                ok=False,
                message="Could not save the configuration.",
                detail=str(error),
            )
        GLib.idle_add(self._on_save_done, outcome)

    def _on_save_done(self, outcome: ActionOutcome) -> bool:
        self._busy = False
        self._set_controls_sensitive(True)

        if outcome.cancelled:
            return _DONE
        if not outcome.ok:
            self._show_toast(outcome.message)
            return _DONE

        self.close()
        self._on_saved(outcome.message)
        return _DONE

    def _detect(self) -> None:
        if self._busy:
            return
        host = self._server.get_text()
        port = self._port.get_text()
        self._busy = True
        self._set_controls_sensitive(False)
        thread = threading.Thread(
            target=self._detect_worker,
            args=(host, port),
            name="yakg-fingerprint",
            daemon=True,
        )
        thread.start()

    def _detect_worker(self, host: str, port: str) -> None:
        try:
            fingerprint, outcome = detect_fingerprint(host, port)
        except Exception as error:
            log.exception("Fingerprint detection failed")
            fingerprint = ""
            outcome = ActionOutcome(
                ok=False,
                message="Could not read the server certificate.",
                detail=str(error),
            )
        GLib.idle_add(self._on_detect_done, fingerprint, outcome)

    def _on_detect_done(self, fingerprint: str, outcome: ActionOutcome) -> bool:
        self._busy = False
        self._set_controls_sensitive(True)
        if outcome.cancelled:
            return _DONE
        if not outcome.ok:
            self._show_toast(outcome.message)
            return _DONE
        self._fingerprint.set_text(fingerprint)
        return _DONE

    def _set_controls_sensitive(self, sensitive: bool) -> None:
        enabled = sensitive and not self._busy
        self._save_button.set_sensitive(enabled)
        self._detect_row.set_sensitive(enabled)
        for widget in (self._server, self._port, self._username, self._fingerprint, self._password):
            widget.set_sensitive(enabled)

    def _show_toast(self, message: str) -> None:
        if message:
            self._toast_overlay.add_toast(Adw.Toast(title=message))


def _reload_if_running(saved: ActionOutcome) -> ActionOutcome:
    """Reload the Kerio service after a successful save, if it is up."""
    manager = ServiceManager()
    status = manager.get_service_status()
    if status.active is not True:
        return saved

    reloaded = manager.reload_service()
    if reloaded.ok:
        return saved
    if reloaded.cancelled:
        return ActionOutcome(
            ok=True,
            message="Saved the configuration. The service was not reloaded.",
            detail=reloaded.detail,
        )
    return ActionOutcome(
        ok=True,
        message="Saved the configuration, but could not reload the service.",
        detail=reloaded.detail,
    )
