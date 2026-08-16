# Verified Kerio Control VPN Client facts

YAKG must never guess how the Kerio client behaves. This file records what has
actually been verified, and where each fact came from, so that code can cite a
source instead of an assumption. Anything not listed here as verified is either
listed as unresolved at the bottom or is not known at all.

If you extend YAKG to a Kerio version other than the one below, re-verify these
facts against that installation and add the findings here.

## Reference installation

| Property | Value |
| --- | --- |
| Distribution | Ubuntu 24.04.4 LTS |
| Package | `kerio-control-vpnclient` |
| Version | `9.6.1.9306.0.patch1-1` |
| Architecture | amd64 |
| Verified on | 2026-08-16 |

Provenance: `dpkg -l kerio-control-vpnclient`.

## Files shipped by the package

Provenance: `dpkg -L kerio-control-vpnclient`.

| Path | Role |
| --- | --- |
| `/usr/sbin/kvpncsvc` | The VPN client daemon. The only executable in the package. |
| `/etc/init.d/kerio-kvc` | SysV init script. |
| `/lib/systemd/system/kerio-kvc.service` | systemd unit. |
| `/lib/systemd/system-generators/kerio-kvc.generator` | Creates the runtime and log directories. |
| `/usr/lib/libkvnet.so` | LGPL virtual-network library used by the daemon. |
| `/usr/share/doc/kerio-control-vpnclient/README` | Vendor documentation. |

`/etc/kerio-kvc.conf` is **not** in the package file list. It is generated at
configure time by the maintainer script, which is why it survives reinstalls but
is absent on a never-configured system.

## Service

Provenance: `/lib/systemd/system/kerio-kvc.service` and `/etc/init.d/kerio-kvc`.

- Unit name: `kerio-kvc.service`.
- Aliases declared in `[Install]`: `kerio-vpn`, `kerio-control-vpn`,
  `kerio-control-vpnclient`. A user may therefore refer to the service by
  several names, but the canonical unit is `kerio-kvc.service`.
- `Type=forking`, `Restart=always`, `RestartSec=5`.
- Start command: `/usr/sbin/kvpncsvc /var/lib/kerio-control-vpn`, with output
  piped to `logger`.
- Reload: `/usr/bin/pkill -SIGHUP kvpncsvc`.
- The init script determines liveness with `pgrep kvpncsvc`, and stops the
  daemon with a plain `kill`. YAKG mirrors that liveness check rather than
  inventing its own.

The unit is installed but ships **disabled**; the package enables it through
`update-rc.d` / `invoke-rc.d` in `postinst`.

### Privileges

Provenance: `/usr/share/polkit-1/actions/org.freedesktop.systemd1.policy` on
Ubuntu 24.04, polkit 124.

`systemctl start|stop|restart kerio-kvc.service` is authorised by
`org.freedesktop.systemd1.manage-units`. `systemctl enable|disable` is
authorised by `org.freedesktop.systemd1.manage-unit-files`. Both default to
`auth_admin_keep` for an active local session. YAKG therefore runs `systemctl`
as the logged-in user and lets systemd and the session agent handle the prompt.
It does not ship a helper or a policy file.

The SysV path has no equivalent polkit action, so YAKG uses `pkexec` on
`/etc/init.d/kerio-kvc` and `/usr/sbin/update-rc.d`. That path has **not** been
verified on a host that actually boots with SysV init.

### `ExecStart` and the shell

The unit file's `ExecStart` line is:

```
/usr/sbin/kvpncsvc /var/lib/kerio-control-vpn 2>&1 | logger -p daemon.err -t kerio-control-vpnclient 2>/dev/null
```

systemd does not invoke a shell for `ExecStart`, so the `2>&1 | logger ...`
tokens are passed to the daemon as extra arguments. The daemon takes its
runtime directory from the first argument, which was confirmed directly
(`kvpncsvc` creates that directory when given one). That it ignores the rest
is inference from the lack of a shell and from the daemon treating a lone
`--help` as a directory name; it is not a verified behavioural claim.

## Configuration

Provenance: `/var/lib/dpkg/info/kerio-control-vpnclient.postinst`, which writes
the file, and the vendor README.

Location: `/etc/kerio-kvc.conf`, mode `0600`, owned by root.

```xml
<config>
  <connections>
    <connection type="persistent">
      <server>vpn.example.com</server>
      <port>4090</port>
      <username>someone</username>
      <password>XOR:3a3b3c</password>
      <fingerprint>AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99</fingerprint>
      <active>1</active>
    </connection>
  </connections>
</config>
```

- Default port is `4090` when the user gives a bare hostname. The wizard splits
  a `host:port` string on the last colon.
- The fingerprint is the **MD5** digest of the server certificate, formatted as
  colon-separated uppercase hex. The wizard obtains it with
  `openssl s_client -connect host:port | openssl x509 -fingerprint -md5 -noout`.
- After editing the file, the configuration must be reloaded
  (`SIGHUP`, or `/etc/init.d/kerio-kvc reload`).

### The `XOR:` password encoding is obfuscation, not encryption

The `postinst` script builds the stored password like this:

```sh
for i in `echo -n "$PASSWORD" | od -t d1 -A n`; do XOR=$(printf "%s%02x" "$XOR" $((i ^ 85))); done
```

Each byte of the password is XORed with the constant `85` (`0x55`) and
hex-encoded. The transform is symmetric and keyless, so anyone who can read
`/etc/kerio-kvc.conf` can recover the plaintext password immediately. The `0600`
root ownership is the only thing actually protecting it.

YAKG treats this file as credential-bearing plaintext, and this limitation is
stated in the README security section rather than glossed over.

## Connecting and disconnecting

**There is no connect or disconnect command.** The vendor README documents only
`/etc/init.d/kerio-kvc {start|stop|restart}` plus `reload`, and `kvpncsvc`
exposes no usable CLI: it rejects `--help` and `-h` with
`Failed to initialize vpn driver.` and exits.

The daemon establishes the connection by itself, from the configuration, as soon
as it starts. A string in the binary makes the coupling explicit:

```
Disconnect cannot be executed - VPN is persistent and configuration still contains active - 1.
```

So a `type="persistent"` connection with `<active>1</active>` refuses to
disconnect while the configuration still asks for it to be up. This leaves two
possible implementations of a Disconnect button:

1. Stop the service.
2. Rewrite the configuration with `<active>0</active>` and reload.

**YAKG uses option 1.** Connect maps to starting `kerio-kvc.service` and
Disconnect maps to stopping it. Option 2 would require a privileged write to a
root-owned credential file on every single click, which is a far larger risk for
no user-visible benefit.

## Logs

Provenance: vendor README, plus the on-disk directory on 2026-08-16.

Directory `/var/log/kerio-kvc/`, mode `0755`, files mode `0644` — readable
without root.

| File | Present on the reference machine | Contents |
| --- | --- | --- |
| `init.log` | **No.** The vendor README names it; it was not created. | Daemon start and stop, if present. |
| `error.log` | Yes | Critical errors. |
| `debug.log` | Yes | Detailed status, including VpnFSM. |

Sibling `.idx` files exist next to the logs; they are an internal index and
YAKG ignores them. `/var/lib/kerio-control-vpn/logs` is a symlink to this
directory, created by the init script and the systemd generator.

### Line format

Verified from `debug.log` and `error.log`:

```
[16/Aug/2026 11:29:55] {channel} message
```

`error.log` uses the same timestamp but a `main:` / `libVPN:` / `vpncore:` prefix
instead of `{channel}`.

### Connection-state markers

`{engine}` VpnFSM is the source of truth. Observed terminal lines:

```
VpnFSM: Saving and sending status to Engine (connected).
VpnFSM: Saving and sending status to Engine (disconnected).
VpnFSM: Saving and sending status to Engine (error).
VpnFSM: Sending status to Engine (disconnecting).
```

A successful connect, in order:

```
VpnFSM: Connect().
VPNClient[0001] - connecting to HOST:PORT, username …
VPNClient[0001] - SSL connection successfully established
Creating new VPN connection: local IP A.B.C.D/…, DNS …
Callback StatusChanged called from libVpn(connected).
VpnFSM: Saving and sending status to Engine (connected).
```

Session markers on `{info}`:

```
Kerio Control VPN Client Service 9.6.1 patch 1 build 9306 started.
Kerio Control VPN Client Service 9.6.1 patch 1 build 9306 stopped.
```

State must be taken from lines **after the last `started.`**. A stopped service
is disconnected even if an older `(connected)` line is still in the file.

### Reconnect

On failure the daemon does not stay down. Observed:

```
Callback StatusChanged called from libVpn(error 145).
VpnFSM: Saving and sending status to Engine (error).
WorkerThread - replan reconnect in 30s.
VpnFSM: Connect().
```

A running service whose last FSM status is `error` is therefore still
connecting, not disconnected.

### Mapped errors

From `error.log` and `libVpn(error N)` in `debug.log`:

| Signal | Meaning |
| --- | --- |
| `145` / `Operation timeout(145)` | Operation timeout |
| `142` / `Connection closed by a remote peer(142)` | Peer closed the connection |
| `167` / `Unable to establish data tunnel (UDP traffic is probably blocked)(167)` | UDP tunnel failed |
| `Network is unreachable` | No route to the server |
| `another instance of Kerio Control VPN Client is running` | Second instance refused |

The `connecting to HOST:PORT, username …` line includes a username. YAKG must
not display or log that field.

## Unresolved

- **Network interface name when connected.** Logs only report `ifIndex = N` and
  a MAC. `libkvnet.so` references both `/dev/kvnet` and `/dev/net/tun`. Do not
  invent a name such as `kvnet0`.
- **Behaviour of multiple `<connection>` elements.** The vendor README states
  that only one VPN connection at a time is supported, and the wizard writes
  exactly one element. Whether additional elements are tolerated or rejected is
  untested, so YAKG will keep writing exactly one.
