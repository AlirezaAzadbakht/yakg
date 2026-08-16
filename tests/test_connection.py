"""Tests for Kerio debug.log parsing and connection-state inference."""

from __future__ import annotations

from pathlib import Path

from yakg.backend.connection import (
    interpret_log,
    map_error,
    parse_connecting_target,
    parse_fsm_status,
    parse_local_ip,
    parse_log_line,
    read_connection_logs,
    read_log_tail,
)
from yakg.backend.status import infer_connection_state
from yakg.models.connection import ConnectionInfo, ConnectionState
from yakg.models.installation import Installation
from yakg.models.service import ServiceBackendKind, ServiceState

INSTALLED = Installation(installed=True, version="9.6.1.9306.0.patch1-1")
RUNNING = ServiceState(exists=True, backend=ServiceBackendKind.SYSTEMD, active=True)
STOPPED = ServiceState(exists=True, backend=ServiceBackendKind.SYSTEMD, active=False)

STARTED = (
    "[16/Aug/2026 11:29:55] {info} Kerio Control VPN Client Service "
    "9.6.1 patch 1 build 9306 started."
)
STOPPED_LINE = (
    "[16/Aug/2026 12:00:00] {info} Kerio Control VPN Client Service "
    "9.6.1 patch 1 build 9306 stopped."
)
CONNECTING = (
    "[16/Aug/2026 11:30:03] {vpnClient} VPNClient[0001] - connecting to "
    "vpn.example.com:9398, username secret.user"
)
CONNECT_FSM = "[16/Aug/2026 11:30:03] {engine} VpnFSM: Connect()."
SSL_OK = (
    "[16/Aug/2026 11:30:04] {vpnClient} VPNClient[0001] - SSL connection successfully established"
)
NEW_TUNNEL = (
    "[16/Aug/2026 11:30:05] {vpnCore} Creating new VPN connection: "
    "local IP 10.8.0.2/255.255.255.0, DNS 10.8.0.1, 10.8.0.1"
)
CONNECTED = (
    "[16/Aug/2026 11:30:06] {engine} VpnFSM: Saving and sending status to Engine (connected)."
)
DISCONNECTED = (
    "[16/Aug/2026 11:40:00] {engine} VpnFSM: Saving and sending status to Engine (disconnected)."
)
ERROR_FSM = "[16/Aug/2026 11:35:00] {engine} VpnFSM: Saving and sending status to Engine (error)."
ERROR_145 = "[16/Aug/2026 11:35:00] {engine} Callback StatusChanged called from libVpn(error 145)."
RECONNECT = "[16/Aug/2026 11:35:00] {engine} WorkerThread - replan reconnect in 30s."


def _session(*lines: str) -> str:
    return "\n".join(lines) + "\n"


class TestParseLogLine:
    def test_debug_line(self) -> None:
        line = parse_log_line("[16/Aug/2026 11:29:55] {engine} VpnFSM: GetStatus().")

        assert line is not None
        assert line.timestamp == "16/Aug/2026 11:29:55"
        assert line.channel == "engine"
        assert line.message == "VpnFSM: GetStatus()."

    def test_error_line_without_channel(self) -> None:
        line = parse_log_line("[16/Aug/2026 11:38:43] main: something failed.")

        assert line is not None
        assert line.channel == ""
        assert line.message == "main: something failed."

    def test_malformed_line_is_ignored(self) -> None:
        assert parse_log_line("not a kerio line") is None


class TestParsers:
    def test_fsm_tokens(self) -> None:
        assert parse_fsm_status("Saving and sending status to Engine (connected).") == "connected"
        assert parse_fsm_status("Sending status to Engine (disconnecting).") == "disconnecting"
        assert parse_fsm_status("VpnFSM: Connect().") is None

    def test_connecting_target_drops_username(self) -> None:
        target = parse_connecting_target(
            "VPNClient[0001] - connecting to vpn.example.com:9398, username secret.user"
        )

        assert target == ("vpn.example.com", 9398)

    def test_local_ip_from_create_line_only(self) -> None:
        created = "Creating new VPN connection: local IP 10.8.0.2/255.255.255.0, DNS 1.1.1.1"
        assert parse_local_ip(created) == "10.8.0.2"
        assert parse_local_ip("received R_IPCONFIG V3 message, IP = 10.8.0.2.255.255.0") is None


class TestMapError:
    def test_known_codes(self) -> None:
        assert "timed out" in map_error("libVpn(error 145)").lower()
        assert "closed" in map_error("Connection closed by a remote peer(142)").lower()
        assert "UDP" in map_error("Unable to establish data tunnel (UDP blocked)(167)")

    def test_unreachable_and_instance(self) -> None:
        unreachable = "Unable to send VPN keepalive: (101) Network is unreachable"
        assert "unreachable" in map_error(unreachable).lower()
        assert (
            "already running"
            in map_error(
                "failed to start because another instance of Kerio Control VPN Client is running."
            ).lower()
        )

    def test_unknown_error_code(self) -> None:
        assert map_error("Callback StatusChanged called from libVpn(error 999).") == (
            "The VPN connection failed."
        )

    def test_ordinary_line_is_not_an_error(self) -> None:
        assert map_error("VpnFSM: GetStatus().") == ""


class TestInterpretLog:
    def test_connected_session(self) -> None:
        info = interpret_log(
            _session(STARTED, CONNECT_FSM, CONNECTING, SSL_OK, NEW_TUNNEL, CONNECTED)
        )

        assert info.fsm == "connected"
        assert info.server == "vpn.example.com"
        assert info.port == 9398
        assert info.local_ip == "10.8.0.2"
        assert info.last_error == ""
        assert "secret.user" not in info.detail

    def test_username_is_not_stored(self) -> None:
        info = interpret_log(_session(STARTED, CONNECTING))

        assert info.server == "vpn.example.com"
        assert "secret" not in repr(info)

    def test_stale_connected_before_last_start_is_ignored(self) -> None:
        info = interpret_log(_session(CONNECTED, STOPPED_LINE, STARTED, CONNECT_FSM, CONNECTING))

        assert info.fsm == ""
        assert info.server == "vpn.example.com"
        assert info.local_ip is None

    def test_error_then_reconnect(self) -> None:
        info = interpret_log(
            _session(STARTED, CONNECT_FSM, ERROR_145, ERROR_FSM, RECONNECT, CONNECT_FSM)
        )

        assert info.fsm == "error"
        assert "timed out" in info.last_error.lower()

    def test_error_is_cleared_by_later_connected(self) -> None:
        info = interpret_log(_session(STARTED, ERROR_145, ERROR_FSM, CONNECTED, NEW_TUNNEL))

        assert info.fsm == "connected"
        assert info.last_error == ""

    def test_no_started_marker_makes_no_fsm_claim(self) -> None:
        info = interpret_log(_session(CONNECTED, CONNECTING))

        assert info.fsm == ""
        assert info.server is None


class TestReadLogTail:
    def test_missing_file(self, tmp_path: Path) -> None:
        assert read_log_tail(tmp_path / "absent.log") == ""

    def test_reads_only_the_tail(self, tmp_path: Path) -> None:
        path = tmp_path / "debug.log"
        path.write_text(("old session\n" * 200) + STARTED + "\n" + CONNECTED + "\n")

        text = read_log_tail(path, max_bytes=len(CONNECTED) + 5)

        assert "Engine (connected)" in text
        assert "started." not in text


class TestReadConnectionLogs:
    def test_missing_directory(self) -> None:
        assert read_connection_logs(None).fsm == ""

    def test_reads_debug_log(self, tmp_path: Path) -> None:
        (tmp_path / "debug.log").write_text(_session(STARTED, CONNECTING, CONNECTED))

        info = read_connection_logs(tmp_path)

        assert info.fsm == "connected"
        assert info.server == "vpn.example.com"


class TestInferConnectionState:
    def test_stopped_service_ignores_stale_connected(self) -> None:
        info = infer_connection_state(
            INSTALLED,
            STOPPED,
            ConnectionInfo(fsm="connected", server="vpn.example.com", local_ip="10.8.0.2"),
        )

        assert info.state is ConnectionState.DISCONNECTED
        assert info.local_ip is None
        assert info.server == "vpn.example.com"

    def test_running_and_connected(self) -> None:
        info = infer_connection_state(
            INSTALLED,
            RUNNING,
            ConnectionInfo(fsm="connected", server="vpn.example.com", local_ip="10.8.0.2"),
        )

        assert info.state is ConnectionState.CONNECTED
        assert info.local_ip == "10.8.0.2"

    def test_running_and_error_is_still_connecting(self) -> None:
        info = infer_connection_state(
            INSTALLED,
            RUNNING,
            ConnectionInfo(fsm="error", last_error="The VPN connection timed out."),
        )

        assert info.state is ConnectionState.CONNECTING
        assert "timed out" in info.last_error

    def test_running_after_connect_call_only(self) -> None:
        info = infer_connection_state(INSTALLED, RUNNING, ConnectionInfo(fsm=""))

        assert info.state is ConnectionState.CONNECTING

    def test_running_disconnected_is_connecting_because_it_retries(self) -> None:
        info = infer_connection_state(INSTALLED, RUNNING, ConnectionInfo(fsm="disconnected"))

        assert info.state is ConnectionState.CONNECTING

    def test_unknown_service_stays_unknown(self) -> None:
        info = infer_connection_state(
            INSTALLED,
            ServiceState(exists=True, backend=ServiceBackendKind.SYSTEMD, active=None),
        )

        assert info.state is ConnectionState.UNKNOWN

    def test_missing_client_is_unknown(self) -> None:
        info = infer_connection_state(Installation(installed=False), ServiceState(exists=False))

        assert info.state is ConnectionState.UNKNOWN
