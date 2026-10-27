"""Settings dialog: edit one YAKG profile.

Load, save, delete and fingerprint detection run on worker threads. This
module never executes a system command itself and never calls xor_decode.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, GLib, Gtk

from yakg.backend.activate import activate_profile, resolve_profile_password
from yakg.backend.config import ConfigError, validate_fingerprint, validate_host, validate_port
from yakg.backend.constants import DEFAULT_PORT
from yakg.backend.fingerprint import detect_fingerprint
from yakg.backend.profiles import (
    default_store,
    delete_profile,
    get_active_id,
    new_profile_id,
    save_profile,
    validate_profile_name,
    validate_username,
)
from yakg.backend.routes import parse_routes
from yakg.backend.secrets import default_secret_store
from yakg.backend.service import ServiceManager
from yakg.models.action import ActionOutcome
from yakg.models.profile import Profile
from yakg.utils.logging import get_logger

log = get_logger(__name__)

_DONE: bool = GLib.SOURCE_REMOVE


class SettingsWindow(Adw.Window):
    """Modal form for one named profile."""

    def __init__(
        self,
        parent: Gtk.Window,
        *,
        profile: Profile | None,
        on_saved: Callable[[str], None],
        on_deleted: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.set_title("New profile" if profile is None else "Settings")
        self.set_default_size(480, 600)

        self._on_saved = on_saved
        self._on_deleted = on_deleted
        self._profile = profile
        self._has_password = False
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

        self._delete_button = Gtk.Button(label="Delete")
        self._delete_button.add_css_class("destructive-action")
        self._delete_button.set_visible(self._profile is not None)
        self._delete_button.connect("clicked", lambda _button: self._delete())
        header.pack_end(self._delete_button)

        self._save_button = Gtk.Button(label="Save")
        self._save_button.add_css_class("suggested-action")
        self._save_button.connect("clicked", lambda _button: self._save())
        header.pack_end(self._save_button)

        return header

    def _build_loading_page(self) -> Gtk.Widget:
        spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32)
        page = Adw.StatusPage(title="Loading profile…")
        page.set_child(spinner)
        return page

    def _build_form(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(title="VPN connection")

        self._name = Adw.EntryRow(title="Name")
        self._server = Adw.EntryRow(title="Server")
        self._port = Adw.EntryRow(title="Port")
        self._port.set_text(str(DEFAULT_PORT))
        self._username = Adw.EntryRow(title="Username")
        self._fingerprint = Adw.EntryRow(title="Fingerprint")
        self._routes = Adw.EntryRow(title="VPN routes (e.g. 192.168.70.0/24)")

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
            self._name,
            self._server,
            self._port,
            self._username,
            password_row,
            self._fingerprint,
            self._detect_row,
            self._routes,
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
        thread = threading.Thread(target=self._load_worker, name="yakg-profile-read", daemon=True)
        thread.start()

    def _load_worker(self) -> None:
        has_password = False
        if self._profile is not None:
            try:
                has_password = default_secret_store().contains(self._profile.id)
            except Exception:
                log.exception("Could not query the secret store")
                has_password = False
        GLib.idle_add(self._on_load_done, has_password)

    def _on_load_done(self, has_password: bool) -> bool:
        self._busy = False
        self._has_password = has_password
        self._apply_profile(self._profile)
        self._stack.set_visible_child_name("form")
        self._set_controls_sensitive(True)
        return _DONE

    def _apply_profile(self, profile: Profile | None) -> None:
        if profile is None:
            self._name.set_text("")
            self._server.set_text("")
            self._port.set_text(str(DEFAULT_PORT))
            self._username.set_text("")
            self._fingerprint.set_text("")
            self._routes.set_text("")
        else:
            self._name.set_text(profile.name)
            self._server.set_text(profile.server)
            self._port.set_text(str(profile.port or DEFAULT_PORT))
            self._username.set_text(profile.username)
            self._fingerprint.set_text(profile.fingerprint)
            self._routes.set_text(", ".join(profile.routes))
        self._password.set_text("")

    def _profile_from_form(self) -> Profile:
        profile_id = self._profile.id if self._profile is not None else new_profile_id()
        return Profile(
            id=profile_id,
            name=validate_profile_name(self._name.get_text()),
            server=validate_host(self._server.get_text()),
            port=validate_port(self._port.get_text()),
            username=validate_username(self._username.get_text()),
            fingerprint=validate_fingerprint(self._fingerprint.get_text()),
            connection_type=(
                self._profile.connection_type if self._profile is not None else "persistent"
            ),
            routes=parse_routes(self._routes.get_text()),
        )

    def _save(self) -> None:
        if self._busy:
            return
        password = self._password.get_text()
        if not password and not self._has_password:
            self._show_toast("A password is required.")
            return
        try:
            profile = self._profile_from_form()
        except ConfigError as error:
            self._show_toast(str(error))
            return

        self._busy = True
        self._set_controls_sensitive(False)
        thread = threading.Thread(
            target=self._save_worker,
            args=(profile, password),
            name="yakg-profile-write",
            daemon=True,
        )
        thread.start()

    def _save_worker(self, profile: Profile, password: str) -> None:
        try:
            outcome = _save_profile(profile, password)
        except Exception as error:
            log.exception("Saving the profile failed")
            outcome = ActionOutcome(
                ok=False,
                message="Could not save the profile.",
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

    def _delete(self) -> None:
        if self._busy or self._profile is None:
            return
        self._busy = True
        self._set_controls_sensitive(False)
        thread = threading.Thread(
            target=self._delete_worker,
            args=(self._profile.id,),
            name="yakg-profile-delete",
            daemon=True,
        )
        thread.start()

    def _delete_worker(self, profile_id: str) -> None:
        try:
            delete_profile(default_store(), profile_id)
            default_secret_store().delete(profile_id)
            outcome = ActionOutcome(ok=True, message="Deleted the profile.")
        except Exception as error:
            log.exception("Deleting the profile failed")
            outcome = ActionOutcome(
                ok=False,
                message="Could not delete the profile.",
                detail=str(error),
            )
        GLib.idle_add(self._on_delete_done, profile_id, outcome)

    def _on_delete_done(self, profile_id: str, outcome: ActionOutcome) -> bool:
        self._busy = False
        self._set_controls_sensitive(True)
        if not outcome.ok:
            self._show_toast(outcome.message)
            return _DONE
        self.close()
        if self._on_deleted is not None:
            self._on_deleted(profile_id)
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
        self._delete_button.set_sensitive(enabled)
        self._detect_row.set_sensitive(enabled)
        for widget in (
            self._name,
            self._server,
            self._port,
            self._username,
            self._fingerprint,
            self._routes,
            self._password,
        ):
            widget.set_sensitive(enabled)

    def _show_toast(self, message: str) -> None:
        if message:
            self._toast_overlay.add_toast(Adw.Toast(title=message))


def _save_profile(profile: Profile, password: str) -> ActionOutcome:
    store = default_store()
    secrets = default_secret_store()
    saved = save_profile(store, profile)
    if password:
        stored = secrets.set(saved.id, password)
        if not stored.ok:
            return stored

    active_id = get_active_id(store)
    running = ServiceManager().get_service_status().active is True
    if saved.id == active_id and running:
        secret = password
        if not secret:
            secret, lookup = resolve_profile_password(secrets, saved.id)
            if not lookup.ok:
                return lookup
        return activate_profile(saved, secret, store=store)
    return ActionOutcome(ok=True, message="Saved the profile.")
