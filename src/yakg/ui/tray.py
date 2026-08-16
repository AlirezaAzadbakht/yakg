"""StatusNotifierItem tray over Gio D-Bus.

Fails closed when no StatusNotifierWatcher is on the session bus. This
module is not imported by tests or by the backend.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gio, GLib

from yakg import APP_ID, APP_NAME
from yakg.backend.tray_spec import (
    MENU_INTERFACE_XML,
    MENU_PATH,
    MENU_PRIMARY,
    MENU_QUIT,
    MENU_ROOT,
    MENU_SHOW,
    SNI_INTERFACE_XML,
    SNI_PATH,
    icon_name,
    menu_entries,
    tooltip_text,
)
from yakg.models.connection import ConnectionState
from yakg.models.snapshot import SystemSnapshot
from yakg.utils.logging import get_logger

log = get_logger(__name__)

_WATCHER_NAME = "org.kde.StatusNotifierWatcher"
_WATCHER_PATH = "/StatusNotifierWatcher"
_WATCHER_IFACE = "org.kde.StatusNotifierWatcher"
_SNI_IFACE = "org.kde.StatusNotifierItem"
_MENU_IFACE = "com.canonical.dbusmenu"


class StatusNotifierTray:
    """A tray icon and a flat DBusMenu, or a no-op if the host is missing."""

    def __init__(
        self,
        *,
        on_show: Callable[[], None],
        on_connect: Callable[[], None],
        on_disconnect: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self._on_show = on_show
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_quit = on_quit
        self._state = ConnectionState.UNKNOWN
        self._server = ""
        self._awaiting = False
        self._revision = 1
        self._connection: Gio.DBusConnection | None = None
        self._sni_id = 0
        self._menu_id = 0
        self._published = False

    @property
    def published(self) -> bool:
        return self._published

    def publish(self) -> bool:
        """Export the item and register with the watcher. False if none."""
        connection: Gio.DBusConnection | None = None
        try:
            connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            sni_info = Gio.DBusNodeInfo.new_for_xml(SNI_INTERFACE_XML)
            menu_info = Gio.DBusNodeInfo.new_for_xml(MENU_INTERFACE_XML)
            if sni_info.interfaces is None or menu_info.interfaces is None:
                return False
            self._connection = connection
            self._sni_id = connection.register_object(
                SNI_PATH,
                sni_info.interfaces[0],
                self._on_sni_method,
                self._on_sni_get_property,
                None,
            )
            self._menu_id = connection.register_object(
                MENU_PATH,
                menu_info.interfaces[0],
                self._on_menu_method,
                self._on_menu_get_property,
                None,
            )
            proxy = Gio.DBusProxy.new_sync(
                connection,
                Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES
                | Gio.DBusProxyFlags.DO_NOT_CONNECT_SIGNALS,
                None,
                _WATCHER_NAME,
                _WATCHER_PATH,
                _WATCHER_IFACE,
                None,
            )
            proxy.call_sync(
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (SNI_PATH,)),
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            )
        except Exception:
            log.info("No StatusNotifier host; the tray icon will not appear")
            self.unpublish()
            return False

        self._published = True
        log.info("Registered a StatusNotifierItem")
        return True

    def unpublish(self) -> None:
        """Drop the exported objects. Safe to call more than once."""
        connection = self._connection
        if connection is not None:
            if self._sni_id:
                connection.unregister_object(self._sni_id)
            if self._menu_id:
                connection.unregister_object(self._menu_id)
        self._sni_id = 0
        self._menu_id = 0
        self._connection = None
        self._published = False

    def update(self, snapshot: SystemSnapshot, *, awaiting_connect: bool = False) -> None:
        """Refresh icon, tooltip and menu from ``snapshot``."""
        self._state = snapshot.connection.state
        self._server = snapshot.connection.server_display
        self._awaiting = awaiting_connect
        self._revision += 1
        if not self._published or self._connection is None:
            return
        self._emit(_SNI_IFACE, SNI_PATH, "NewIcon", None)
        self._emit(_SNI_IFACE, SNI_PATH, "NewToolTip", None)
        self._emit(_SNI_IFACE, SNI_PATH, "NewStatus", GLib.Variant("(s)", (self._status(),)))
        self._emit(
            _MENU_IFACE,
            MENU_PATH,
            "LayoutUpdated",
            GLib.Variant("(ui)", (self._revision, MENU_ROOT)),
        )

    def _status(self) -> str:
        if self._state is ConnectionState.CONNECTING or self._awaiting:
            return "NeedsAttention"
        return "Active"

    def _emit(
        self,
        interface: str,
        path: str,
        signal: str,
        parameters: GLib.Variant | None,
    ) -> None:
        if self._connection is None:
            return
        try:
            self._connection.emit_signal(None, path, interface, signal, parameters)
        except Exception:
            log.info("Could not emit tray signal %s", signal)

    def _on_sni_method(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _object_path: str,
        _interface_name: str,
        method_name: str,
        _parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        if method_name in {"Activate", "SecondaryActivate"}:
            self._on_show()
        invocation.return_value(None)

    def _on_sni_get_property(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _object_path: str,
        _interface_name: str,
        property_name: str,
    ) -> GLib.Variant | None:
        values: dict[str, GLib.Variant] = {
            "Category": GLib.Variant("s", "SystemServices"),
            "Id": GLib.Variant("s", APP_ID),
            "Title": GLib.Variant("s", APP_NAME),
            "Status": GLib.Variant("s", self._status()),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", icon_name(self._state)),
            "IconThemePath": GLib.Variant("s", ""),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
            "ToolTip": GLib.Variant(
                "(sa{sv}ss)",
                (icon_name(self._state), {}, APP_NAME, tooltip_text(self._state, self._server)),
            ),
        }
        return values.get(property_name)

    def _on_menu_method(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _object_path: str,
        _interface_name: str,
        method_name: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        if method_name == "GetLayout":
            invocation.return_value(GLib.Variant("(u(ia{sv}av))", self._layout()))
            return
        if method_name == "GetGroupProperties":
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (self._group_properties(),)))
            return
        if method_name == "GetProperty":
            item_id, name = parameters.unpack()
            value = self._item_property(int(item_id), str(name))
            invocation.return_value(GLib.Variant("(v)", (value,)))
            return
        if method_name == "Event":
            item_id, event_id, _data, _timestamp = parameters.unpack()
            if event_id == "clicked":
                self._handle_menu(int(item_id))
            invocation.return_value(None)
            return
        if method_name == "EventGroup":
            events = parameters.unpack()[0]
            for item_id, event_id, _data, _timestamp in events:
                if event_id == "clicked":
                    self._handle_menu(int(item_id))
            invocation.return_value(GLib.Variant("(ai)", ([],)))
            return
        if method_name == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
            return
        if method_name == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
            return
        invocation.return_dbus_error(
            "org.freedesktop.DBus.Error.UnknownMethod",
            f"Unknown method {method_name}",
        )

    def _on_menu_get_property(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _object_path: str,
        _interface_name: str,
        property_name: str,
    ) -> GLib.Variant | None:
        values = {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }
        return values.get(property_name)

    def _handle_menu(self, item_id: int) -> None:
        if item_id == MENU_SHOW:
            self._on_show()
        elif item_id == MENU_PRIMARY:
            if (
                self._state is ConnectionState.CONNECTED
                or self._state is ConnectionState.CONNECTING
                or self._awaiting
            ):
                self._on_disconnect()
            else:
                self._on_connect()
        elif item_id == MENU_QUIT:
            self._on_quit()

    def _layout(self) -> tuple[int, tuple[int, dict[str, GLib.Variant], list[GLib.Variant]]]:
        entries = menu_entries(self._state, self._awaiting)
        children = [_menu_node(item_id, props) for item_id, props in entries]
        root_props = {"children-display": GLib.Variant("s", "submenu")}
        return (self._revision, (MENU_ROOT, root_props, children))

    def _group_properties(self) -> list[tuple[int, dict[str, GLib.Variant]]]:
        items = [(MENU_ROOT, {"children-display": GLib.Variant("s", "submenu")})]
        for item_id, props in menu_entries(self._state, self._awaiting):
            items.append((item_id, _variants(props)))
        return items

    def _item_property(self, item_id: int, name: str) -> GLib.Variant:
        if item_id == MENU_ROOT and name == "children-display":
            return GLib.Variant("s", "submenu")
        for entry_id, props in menu_entries(self._state, self._awaiting):
            if entry_id == item_id and name in props:
                return _variant(props[name])
        return GLib.Variant("s", "")


def _variants(props: dict[str, object]) -> dict[str, GLib.Variant]:
    return {key: _variant(value) for key, value in props.items()}


def _variant(value: object) -> GLib.Variant:
    if isinstance(value, bool):
        return GLib.Variant("b", value)
    return GLib.Variant("s", str(value))


def _menu_node(item_id: int, props: dict[str, object]) -> GLib.Variant:
    return GLib.Variant("(ia{sv}av)", (item_id, _variants(props), []))
