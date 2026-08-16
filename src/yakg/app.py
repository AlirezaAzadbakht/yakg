"""The GTK application object: window lifecycle, actions and accelerators."""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gdk, Gio, Gtk

from yakg import APP_ID, APP_NAME, __version__
from yakg.backend.autostart import ensure_application_desktop
from yakg.backend.notify import NotifyEvent, next_notification
from yakg.backend.preferences import load_preferences
from yakg.models.snapshot import SystemSnapshot
from yakg.resources import icon_theme_root
from yakg.ui.tray import StatusNotifierTray
from yakg.ui.window import YakgWindow
from yakg.utils.logging import get_logger

log = get_logger(__name__)

_DISCLAIMER = (
    "YAKG is an independent open-source project and is not affiliated with, "
    "endorsed by, or maintained by GFI Software or Kerio Technologies."
)

_NOTIFICATION_ID = "connection"


class YakgApplication(Adw.Application):
    """Single-window libadwaita application that can keep running hidden."""

    def __init__(self, *, start_hidden: bool = False) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._window: YakgWindow | None = None
        self._tray: StatusNotifierTray | None = None
        self._start_hidden = start_hidden
        self._held = False

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)

        self._add_action("refresh", self._on_refresh, accels=["<Primary>r"])
        self._add_action("settings", self._on_settings, accels=["<Primary>comma"])
        self._add_action("show", self._on_show)
        self._add_action("connect", self._on_connect)
        self._add_action("disconnect", self._on_disconnect)
        self._add_action("about", self._on_about)
        self._add_action("quit", self._on_quit, accels=["<Primary>q"])

        ensure_application_desktop()
        self._register_icon()
        if not self._held:
            self.hold()
            self._held = True

    def do_activate(self) -> None:
        if self._window is None:
            self._window = YakgWindow(application=self)
            self._publish_tray()
            if not self._start_hidden:
                self._window.present()
            return
        self._window.present()

    def on_status(
        self,
        previous: SystemSnapshot | None,
        current: SystemSnapshot,
        *,
        window_visible: bool,
        user_stop: bool,
        timeout: bool = False,
    ) -> None:
        """Update the tray and maybe send a desktop notification."""
        if self._tray is not None:
            awaiting = self._window.awaiting_connect if self._window is not None else False
            self._tray.update(current, awaiting_connect=awaiting)

        if not load_preferences().notifications_enabled:
            return
        event = next_notification(
            previous,
            current,
            window_visible=window_visible,
            user_stop=user_stop,
            timeout=timeout,
        )
        if event is not None:
            self._send_notification(event)

    def _publish_tray(self) -> None:
        tray = StatusNotifierTray(
            on_show=self.show_window,
            on_connect=self.connect_vpn,
            on_disconnect=self.disconnect_vpn,
            on_quit=self.quit_app,
        )
        if tray.publish():
            self._tray = tray

    def show_window(self) -> None:
        """Present the main window, creating it if needed."""
        if self._window is None:
            self.activate()
            return
        self._window.present()

    def connect_vpn(self) -> None:
        if self._window is not None:
            self._window.request_connect()

    def disconnect_vpn(self) -> None:
        if self._window is not None:
            self._window.request_disconnect()

    def quit_app(self) -> None:
        """Tear down the hidden session and exit."""
        if self._tray is not None:
            self._tray.unpublish()
            self._tray = None
        if self._window is not None:
            self._window.shutdown_session()
            self._window.destroy()
            self._window = None
        if self._held:
            self.release()
            self._held = False
        self.quit()

    def _send_notification(self, event: NotifyEvent) -> None:
        notification = Gio.Notification.new(event.title)
        if event.body:
            notification.set_body(event.body)
        notification.set_default_action("app.show")
        self.send_notification(_NOTIFICATION_ID, notification)

    def _register_icon(self) -> None:
        display = Gdk.Display.get_default()
        if display is not None:
            theme = Gtk.IconTheme.get_for_display(display)
            theme.add_search_path(str(icon_theme_root()))
        Gtk.Window.set_default_icon_name(APP_ID)

    def _add_action(
        self,
        name: str,
        callback: object,
        *,
        accels: list[str] | None = None,
    ) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if accels:
            self.set_accels_for_action(f"app.{name}", accels)

    def _on_refresh(self, _action: Gio.SimpleAction, _parameter: None) -> None:
        if self._window is not None:
            self._window.refresh()

    def _on_settings(self, _action: Gio.SimpleAction, _parameter: None) -> None:
        self.show_window()
        if self._window is not None:
            self._window.open_settings()

    def _on_show(self, _action: Gio.SimpleAction, _parameter: None) -> None:
        self.show_window()

    def _on_connect(self, _action: Gio.SimpleAction, _parameter: None) -> None:
        self.connect_vpn()

    def _on_disconnect(self, _action: Gio.SimpleAction, _parameter: None) -> None:
        self.disconnect_vpn()

    def _on_about(self, _action: Gio.SimpleAction, _parameter: None) -> None:
        self.show_window()
        about = Adw.AboutWindow(
            transient_for=self._window,
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=__version__,
            developer_name="The YAKG contributors",
            license_type=Gtk.License.MIT_X11,
            comments=_DISCLAIMER,
            website="https://github.com/alirezaazadbakht/yakg",
        )
        about.present()

    def _on_quit(self, _action: Gio.SimpleAction, _parameter: None) -> None:
        self.quit_app()
