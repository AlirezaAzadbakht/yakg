"""The YAKG main window.

Shows what is installed, what the service is doing, and the controls to start,
stop, restart, enable and disable it.

Two rules shape this module. Nothing here executes a system command; it calls
the backend instead. And nothing blocks the main loop: detection and service
actions run on a worker thread and results come back through ``GLib.idle_add``.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from yakg import APP_NAME
from yakg.backend.service import ServiceManager
from yakg.backend.status import gather_status
from yakg.models.action import ActionOutcome
from yakg.models.connection import ConnectionState
from yakg.models.snapshot import SystemSnapshot
from yakg.utils.logging import get_logger

log = get_logger(__name__)

#: Returned from an idle callback so it runs once and is then dropped.
_DONE: bool = GLib.SOURCE_REMOVE

#: Bullets matching the states in the product sketch: filled when connected,
#: hollow when not, dotted while in transition.
_CONNECTION_MARK = {
    ConnectionState.CONNECTED: "●",
    ConnectionState.DISCONNECTED: "○",
    ConnectionState.CONNECTING: "◌",
    ConnectionState.UNKNOWN: "○",
}

_CONNECTION_STYLE = {
    ConnectionState.CONNECTED: "success",
    ConnectionState.DISCONNECTED: "dim-label",
    ConnectionState.CONNECTING: "warning",
    ConnectionState.UNKNOWN: "dim-label",
}

_INSTALL_HINT = (
    "YAKG manages the Kerio Control VPN Client, which does not appear to be "
    "installed. Install the client package provided by your VPN administrator, "
    "then refresh."
)

_UNKNOWN_SERVICE_HINT = (
    "The service state is unknown, so it cannot be started or stopped from here."
)


class YakgWindow(Adw.ApplicationWindow):
    """Main window: status, a primary Start/Stop button, and a boot switch."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)

        self.set_title(APP_NAME)
        self.set_default_size(420, 520)

        self._refreshing = False
        self._busy = False
        self._snapshot: SystemSnapshot | None = None
        self._action_detail = ""

        self._add_window_actions()

        self._toast_overlay = Adw.ToastOverlay()
        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._stack.add_named(self._build_loading_page(), "loading")
        self._stack.add_named(self._build_missing_page(), "missing")
        self._stack.add_named(self._build_status_page(), "status")
        self._toast_overlay.set_child(self._stack)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._build_header())
        toolbar.set_content(self._toast_overlay)
        self.set_content(toolbar)

        self._stack.set_visible_child_name("loading")
        self.refresh()

    def _add_window_actions(self) -> None:
        self._restart_action = Gio.SimpleAction.new("restart", None)
        self._restart_action.connect("activate", lambda *_args: self.restart_service())
        self.add_action(self._restart_action)

    # Construction -----------------------------------------------------------

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()

        self._refresh_button = Gtk.Button(
            icon_name="view-refresh-symbolic",
            tooltip_text="Refresh (Ctrl+R)",
        )
        self._refresh_button.connect("clicked", lambda _button: self.refresh())
        header.pack_start(self._refresh_button)

        menu = Gio.Menu()
        menu.append("Restart Service", "win.restart")
        menu.append("About YAKG", "app.about")
        menu.append("Quit", "app.quit")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(menu_button)

        return header

    def _build_loading_page(self) -> Gtk.Widget:
        spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32)
        page = Adw.StatusPage(title="Checking your system…")
        page.set_child(spinner)
        return page

    def _build_missing_page(self) -> Gtk.Widget:
        self._missing_page = Adw.StatusPage(
            icon_name="dialog-warning-symbolic",
            title="Kerio VPN Client not found",
            description=_INSTALL_HINT,
        )

        button = Gtk.Button(label="Check Again", halign=Gtk.Align.CENTER)
        button.add_css_class("pill")
        button.add_css_class("suggested-action")
        button.connect("clicked", lambda _button: self.refresh())
        self._missing_page.set_child(button)

        return self._missing_page

    def _build_status_page(self) -> Gtk.Widget:
        self._banner = Adw.Banner(revealed=False)

        self._connection_mark = Gtk.Label(label="○")
        self._connection_mark.add_css_class("title-1")
        self._connection_label = Gtk.Label(label="Unknown")
        self._connection_label.add_css_class("title-2")

        summary = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
            margin_top=24,
            margin_bottom=8,
        )
        summary.append(self._connection_mark)
        summary.append(self._connection_label)

        self._connection_hint = Gtk.Label(halign=Gtk.Align.CENTER, wrap=True)
        self._connection_hint.add_css_class("dim-label")
        self._connection_hint.set_margin_bottom(8)

        self._action_spinner = Gtk.Spinner(visible=False)
        self._action_label = Gtk.Label(label="Start")
        action_contents = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
        )
        action_contents.append(self._action_spinner)
        action_contents.append(self._action_label)

        self._action_button = Gtk.Button(halign=Gtk.Align.CENTER)
        self._action_button.set_child(action_contents)
        self._action_button.add_css_class("pill")
        self._action_button.add_css_class("suggested-action")
        self._action_button.connect("clicked", self._on_primary_clicked)

        self._action_hint = Gtk.Label(halign=Gtk.Align.CENTER, wrap=True, visible=False)
        self._action_hint.add_css_class("dim-label")
        self._action_hint.set_margin_bottom(8)

        details = Adw.PreferencesGroup(title="System")
        self._client_row = Adw.ActionRow(title="Kerio VPN Client", subtitle="Unknown")
        self._config_row = Adw.ActionRow(title="Client configuration", subtitle="Unknown")
        self._service_row = Adw.ActionRow(title="Service", subtitle="Unknown")
        self._boot_row = Adw.SwitchRow(title="Start at boot")
        self._boot_handler = self._boot_row.connect("notify::active", self._on_boot_toggled)
        for row in (self._client_row, self._config_row, self._service_row, self._boot_row):
            details.add(row)

        technical = Adw.PreferencesGroup()
        self._technical_row = Adw.ExpanderRow(title="Technical details")
        self._technical_text = Gtk.Label(
            xalign=0,
            wrap=True,
            selectable=True,
            margin_top=8,
            margin_bottom=8,
            margin_start=12,
            margin_end=12,
        )
        self._technical_text.add_css_class("monospace")
        self._technical_text.add_css_class("dim-label")
        technical_row_content = Adw.ActionRow()
        technical_row_content.set_child(self._technical_text)
        self._technical_row.add_row(technical_row_content)
        technical.add(self._technical_row)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.append(self._banner)
        content.append(summary)
        content.append(self._connection_hint)
        content.append(self._action_button)
        content.append(self._action_hint)
        content.append(details)
        content.append(technical)

        clamp = Adw.Clamp(maximum_size=480, margin_start=12, margin_end=12, margin_bottom=18)
        clamp.set_child(content)

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(clamp)
        return scroller

    # Refresh ----------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read system state on a worker thread."""
        if self._refreshing or self._busy:
            return

        self._refreshing = True
        self._set_controls_sensitive(False)

        thread = threading.Thread(target=self._refresh_worker, name="yakg-detect", daemon=True)
        thread.start()

    def _refresh_worker(self) -> None:
        """Runs off the main thread. Must not touch a widget."""
        try:
            snapshot = gather_status()
        except Exception as error:
            log.exception("Detection failed")
            GLib.idle_add(self._on_refresh_failed, str(error))
        else:
            GLib.idle_add(self._on_refresh_done, snapshot)

    def _on_refresh_done(self, snapshot: SystemSnapshot) -> bool:
        self._refreshing = False
        self._snapshot = snapshot

        if snapshot.installation.installed:
            self._apply(snapshot)
            self._stack.set_visible_child_name("status")
        else:
            self._missing_page.set_description(
                f"{_INSTALL_HINT}\n\n{snapshot.installation.detail}".strip()
            )
            self._stack.set_visible_child_name("missing")
            self._restart_action.set_enabled(False)

        self._set_controls_sensitive(True)
        return _DONE

    def _on_refresh_failed(self, message: str) -> bool:
        self._refreshing = False
        self._set_controls_sensitive(True)

        # The human-readable half goes in the toast; the technical half stays in
        # the details view and the log, never in a raw traceback dialog.
        self._show_toast("Could not check the system state.")
        self._technical_text.set_label(message)
        self._stack.set_visible_child_name("status")

        return _DONE

    # Service actions --------------------------------------------------------

    def restart_service(self) -> None:
        """Restart the Kerio service. Called from the header menu."""
        self._run_action("restart")

    def _on_primary_clicked(self, _button: Gtk.Button) -> None:
        if self._snapshot is None or self._snapshot.service.active is None:
            return
        self._run_action("stop" if self._snapshot.service.active else "start")

    def _on_boot_toggled(self, row: Adw.SwitchRow, _pspec: GObject.ParamSpec) -> None:
        if self._busy or self._refreshing:
            return
        self._run_action("enable" if row.get_active() else "disable")

    def _run_action(self, operation: str) -> None:
        if self._busy or self._refreshing:
            return

        self._busy = True
        self._set_controls_sensitive(False)
        self._action_spinner.set_visible(True)
        self._action_spinner.start()

        thread = threading.Thread(
            target=self._action_worker,
            args=(operation,),
            name=f"yakg-{operation}",
            daemon=True,
        )
        thread.start()

    def _action_worker(self, operation: str) -> None:
        """Runs off the main thread. Must not touch a widget."""
        manager = ServiceManager()
        method = {
            "start": manager.start_service,
            "stop": manager.stop_service,
            "restart": manager.restart_service,
            "enable": manager.enable_service,
            "disable": manager.disable_service,
        }[operation]
        try:
            outcome = method()
        except Exception as error:
            log.exception("Service %s failed", operation)
            outcome = ActionOutcome(
                ok=False,
                message="Could not complete the request.",
                detail=str(error),
            )
        GLib.idle_add(self._on_action_done, outcome)

    def _on_action_done(self, outcome: ActionOutcome) -> bool:
        self._busy = False
        self._action_spinner.stop()
        self._action_spinner.set_visible(False)

        if outcome.cancelled:
            self._action_detail = ""
        else:
            if outcome.message:
                self._show_toast(outcome.message)
            self._action_detail = outcome.detail if not outcome.ok else ""

        # Re-read so the displayed state comes from the system, not a guess.
        self.refresh()
        return _DONE

    # Presentation -----------------------------------------------------------

    def _apply(self, snapshot: SystemSnapshot) -> None:
        installation = snapshot.installation
        service = snapshot.service

        state = snapshot.connection
        self._connection_mark.set_label(_CONNECTION_MARK[state])
        self._connection_label.set_label(state.label)
        for widget in (self._connection_mark, self._connection_label):
            for style in set(_CONNECTION_STYLE.values()):
                widget.remove_css_class(style)
            widget.add_css_class(_CONNECTION_STYLE[state])

        self._connection_hint.set_label(
            "Connection status is not yet available; YAKG can currently report "
            "the service state only."
            if state is ConnectionState.UNKNOWN
            else ""
        )
        self._connection_hint.set_visible(state is ConnectionState.UNKNOWN)

        self._update_primary_button(service.active)

        self._client_row.set_subtitle(f"Version {installation.version_display}")
        self._config_row.set_subtitle(
            f"Found at {installation.config_path}"
            if installation.config_present
            else "Not configured yet"
        )
        self._service_row.set_subtitle(f"{service.running_label} ({service.backend.label})")

        self._boot_row.handler_block(self._boot_handler)
        self._boot_row.set_active(service.enabled_at_boot is True)
        self._boot_row.set_subtitle(service.boot_label)
        self._boot_row.handler_unblock(self._boot_handler)

        self._banner.set_revealed(installation.needs_configuration)
        if installation.needs_configuration:
            self._banner.set_title(
                "The Kerio client has no configuration yet. Run "
                "'sudo dpkg-reconfigure kerio-control-vpnclient' to set one up."
            )

        details = _technical_details(snapshot)
        if self._action_detail:
            details = f"{self._action_detail}\n\n{details}"
        self._technical_text.set_label(details)

        self._set_controls_sensitive(True)

    def _update_primary_button(self, active: bool | None) -> None:
        self._action_button.remove_css_class("suggested-action")
        self._action_button.remove_css_class("destructive-action")

        if active is True:
            self._action_label.set_label("Stop")
            self._action_button.add_css_class("destructive-action")
            self._action_hint.set_visible(False)
        elif active is False:
            self._action_label.set_label("Start")
            self._action_button.add_css_class("suggested-action")
            self._action_hint.set_visible(False)
        else:
            self._action_label.set_label("Start")
            self._action_button.add_css_class("suggested-action")
            self._action_hint.set_label(_UNKNOWN_SERVICE_HINT)
            self._action_hint.set_visible(True)

    def _set_controls_sensitive(self, sensitive: bool) -> None:
        blocked = not sensitive or self._busy or self._refreshing
        service = self._snapshot.service if self._snapshot is not None else None

        can_toggle = (
            not blocked and service is not None and service.exists and service.active is not None
        )
        can_restart = not blocked and service is not None and service.exists
        can_boot = (
            not blocked
            and service is not None
            and service.exists
            and service.enabled_at_boot is not None
        )

        self._refresh_button.set_sensitive(not blocked)
        self._action_button.set_sensitive(can_toggle)
        self._boot_row.set_sensitive(can_boot)
        self._restart_action.set_enabled(can_restart)

    def _show_toast(self, message: str) -> None:
        self._toast_overlay.add_toast(Adw.Toast(title=message))


def _technical_details(snapshot: SystemSnapshot) -> str:
    """Build the contents of the expandable details row."""
    installation = snapshot.installation
    service = snapshot.service

    lines = [
        f"Executable:     {installation.executable or 'not found'}",
        f"Version:        {installation.version_display}",
        f"Detected via:   {installation.detected_via or 'not detected'}",
        f"Configuration:  {installation.config_path or 'not found'}",
        f"Logs:           {installation.log_dir or 'not found'}",
        f"Init system:    {service.backend.label}",
        f"Service unit:   {service.unit_name or 'not found'}",
    ]
    for note in (installation.detail, service.detail):
        if note:
            lines.append(f"\n{note}")
    if service.raw:
        lines.append(f"\n{service.raw}")

    return "\n".join(lines)
