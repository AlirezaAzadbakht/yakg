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

Provenance: vendor README, plus the on-disk directory.

Directory `/var/log/kerio-kvc/`, mode `0755`, files mode `0644` — readable
without root, which is why YAKG can offer a log viewer with no privilege
escalation.

| File | Contents |
| --- | --- |
| `init.log` | Daemon start and stop. |
| `error.log` | Critical errors. |
| `debug.log` | Detailed status and error messages. |

Sibling `.idx` files exist next to the logs; they are an internal index and
YAKG ignores them. `/var/lib/kerio-control-vpn/logs` is a symlink to this
directory, created by the init script and the systemd generator.

## Unresolved

These are genuinely unknown and must not be guessed. They are Milestone 3
concerns and YAKG currently reports connection state as `UNKNOWN`.

- **Network interface name when connected.** `libkvnet.so` references both
  `/dev/kvnet` (its own kernel module) and `/dev/net/tun`, so the interface may
  differ depending on which path the driver takes. The reference machine has no
  `kvnet` module loaded and the daemon was not running, so no interface could be
  observed. Determine this from a live connection before relying on it.
- **`debug.log` line format.** The binary contains several timestamp formats
  (`%04d-%02d-%02d %02d:%02d:%02d` and an ISO-8601 variant), so the actual log
  prefix must be confirmed against a real file before a parser is written.
- **Connection-state log markers.** `Connection established successfully` and
  `Creating new VPN connection: local IP ..., DNS ...` exist in the binary and
  look promising as state signals, but their exact surrounding format has not
  been observed in a real log.
- **Behaviour of multiple `<connection>` elements.** The vendor README states
  that only one VPN connection at a time is supported, and the wizard writes
  exactly one element. Whether additional elements are tolerated or rejected is
  untested, so YAKG will keep writing exactly one.
