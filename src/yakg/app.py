"""The GTK application object: window lifecycle, actions and accelerators."""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gio, Gtk

from yakg import APP_ID, APP_NAME, __version__
from yakg.ui.window import YakgWindow
from yakg.utils.logging import get_logger

log = get_logger(__name__)

_DISCLAIMER = (
    "YAKG is an independent open-source project and is not affiliated with, "
    "endorsed by, or maintained by GFI Software or Kerio Technologies."
)


class YakgApplication(Adw.Application):
    """Single-window libadwaita application."""

    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._window: YakgWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)

        self._add_action("refresh", self._on_refresh, accels=["<Primary>r"])
        self._add_action("settings", self._on_settings, accels=["<Primary>comma"])
        self._add_action("about", self._on_about)
        self._add_action("quit", self._on_quit, accels=["<Primary>q"])

    def do_activate(self) -> None:
        if self._window is None:
            self._window = YakgWindow(application=self)
        self._window.present()

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
        if self._window is not None:
            self._window.open_settings()

    def _on_about(self, _action: Gio.SimpleAction, _parameter: None) -> None:
        about = Adw.AboutWindow(
            transient_for=self._window,
            application_name=APP_NAME,
            application_icon="network-vpn-symbolic",
            version=__version__,
            developer_name="The YAKG contributors",
            license_type=Gtk.License.MIT_X11,
            comments=_DISCLAIMER,
            website="https://github.com/alirezaazadbakht/yakg",
        )
        about.present()

    def _on_quit(self, _action: Gio.SimpleAction, _parameter: None) -> None:
        self.quit()
