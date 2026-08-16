"""Pure tray presentation: icons, labels, introspection XML.

No Gio and no session bus. ``ui.tray`` exports these over D-Bus.
"""

from __future__ import annotations

from yakg.models.connection import ConnectionState

SNI_PATH = "/StatusNotifierItem"
MENU_PATH = "/Menu"

MENU_ROOT = 0
MENU_SHOW = 1
MENU_PRIMARY = 2
MENU_SEPARATOR = 3
MENU_QUIT = 4

SNI_INTERFACE_XML = """\
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ToolTip" type="(sa{sv}ss)" access="read"/>
    <method name="ContextMenu">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="Activate">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="Scroll">
      <arg type="i" name="delta" direction="in"/>
      <arg type="s" name="orientation" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus">
      <arg type="s" name="status"/>
    </signal>
  </interface>
</node>
"""

MENU_INTERFACE_XML = """\
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
  </interface>
</node>
"""


def icon_name(state: ConnectionState) -> str:
    """Theme icon for the tray."""
    if state is ConnectionState.CONNECTED:
        return "network-vpn"
    if state is ConnectionState.CONNECTING:
        return "network-vpn-acquiring"
    return "network-offline"


def tooltip_text(state: ConnectionState, server: str = "") -> str:
    """Tray tooltip. Never includes a username."""
    line = f"YAKG — {state.label}"
    if server:
        return f"{line}\n{server}"
    return line


def primary_label(state: ConnectionState, awaiting_connect: bool = False) -> str:
    """Connect / Cancel / Disconnect, matching the main-window button."""
    if state is ConnectionState.CONNECTED:
        return "Disconnect"
    if state is ConnectionState.CONNECTING or awaiting_connect:
        return "Cancel"
    return "Connect"


def menu_entries(
    state: ConnectionState,
    awaiting_connect: bool = False,
) -> tuple[tuple[int, dict[str, object]], ...]:
    """Flat menu: Show, primary action, separator, Quit."""
    return (
        (MENU_SHOW, {"label": "Show", "enabled": True}),
        (MENU_PRIMARY, {"label": primary_label(state, awaiting_connect), "enabled": True}),
        (MENU_SEPARATOR, {"type": "separator"}),
        (MENU_QUIT, {"label": "Quit", "enabled": True}),
    )
