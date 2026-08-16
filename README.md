# YAKG — Yet Another Kerio GUI

A small, native GNOME desktop frontend for the **Kerio Control VPN Client** on Linux.

> **YAKG is an independent open-source project and is not affiliated with, endorsed by, or
> maintained by GFI Software or Kerio Technologies.**

The Kerio Control VPN Client for Linux is managed entirely from the command line: you
reconfigure it with `dpkg-reconfigure`, start it with `systemctl`, and find out what it is doing
by reading log files. YAKG puts a small window in front of that.

YAKG is **not** a VPN implementation. It does not speak the Kerio protocol and contains no
Kerio code. It drives the client you already have installed.

## Status

Early development. **Milestone 1 (detection) is complete**: YAKG reports what is installed and
what the service is doing. It cannot yet change anything — every operation in this release is
read-only.

| Milestone | Scope | State |
| --- | --- | --- |
| 1 | Detection and status | Done |
| 2 | Service control (start, stop, restart, enable, disable) | Not started |
| 3 | Connection control and real connection state | Not started |
| 4 | Configuration viewing, validation and safe saving | Not started |
| 5 | Profiles | Not started |
| 6 | Tray, notifications, autostart | Not started |

## Screenshots

Not yet available. They will be added once the interface stops changing shape.

## Supported versions

| | Verified | Notes |
| --- | --- | --- |
| Ubuntu | 24.04 LTS | Should work on any release with GTK 4 and libadwaita 1.2+. |
| Desktop | GNOME (Wayland and X11) | Any GTK 4 environment should work. |
| Kerio Control VPN Client | 9.6.1.9306.0 | Other 9.x versions are likely fine; see below. |
| Python | 3.10+ | Developed against 3.12. |

YAKG's knowledge of the Kerio client is recorded in [docs/kerio-facts.md](docs/kerio-facts.md),
with the evidence behind each fact. If you run a different Kerio version, YAKG will still detect
it, and that document is where to record anything that turns out to differ.

Both systemd and SysV init are supported, because the Kerio package ships a unit *and* an init
script. YAKG picks whichever is actually in charge at runtime, so it also works inside containers
and WSL images that do not boot with systemd.

## Installation

YAKG is not packaged yet. Install the GTK bindings from Ubuntu, then YAKG from source:

```bash
sudo apt install python3-pip python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1
git clone https://github.com/alirezaazadbakht/yakg.git
cd yakg
pip install --user .
yakg
```

The GTK bindings deliberately come from apt rather than pip: PyGObject built from PyPI has to
match the system GTK, and installing it from the distribution avoids that whole problem.

## Usage

Launch `yakg`, or find *YAKG* in your applications list. The window shows whether the Kerio
client is installed, whether its service exists, whether it is running, and whether it starts at
boot. Press the refresh button, or Ctrl+R, to re-check.

For troubleshooting, or on a machine with no desktop, the same information is available as text:

```console
$ yakg --detect
YAKG 0.1.0 detection report

Kerio Control VPN Client
  Installed:      yes
  Version:        9.6.1.9306.0.patch1-1
  Executable:     /usr/sbin/kvpncsvc
  Detected via:   dpkg and executable
  Configuration:  /etc/kerio-kvc.conf
  Logs:           /var/log/kerio-kvc

Service
  Found:          yes
  Init system:    systemd
  Unit:           kerio-kvc.service
  Running:        Stopped
  Start at boot:  Disabled

Connection
  State:          Disconnected
```

`yakg --debug` turns on debug logging. Logs are written to `~/.local/state/yakg/yakg.log`.

## Profile management

Not implemented yet; it is Milestone 5.

The Kerio client supports exactly one VPN connection at a time, so YAKG will keep its own
profiles under `~/.config/yakg/profiles/` and translate the active one into the single
configuration the client expects. Activating a profile will always back up the existing
configuration first, and restore it if the write fails.

## Security model

Three things drive YAKG's design here.

**The GUI does not run as root.** There is no `sudo yakg`. The interface runs as your user, and
privileged operations will be narrowly scoped and requested through polkit when Milestone 2
introduces them. Nothing in this release requires any privilege at all.

**Commands are never built as shell strings.** Every external command goes through one runner
that takes an argument vector and never invokes a shell, so no value YAKG handles can be
interpreted as shell syntax. Secrets are passed on standard input rather than in arguments,
because arguments are visible to every user on the machine through `/proc`.

**Logs are scrubbed.** Every log handler carries a redaction filter that removes passwords,
private keys and Kerio's obfuscated password form before anything reaches the disk.

### About the Kerio password

You should know how the Kerio client stores your password, because it affects what YAKG can
promise you.

`/etc/kerio-kvc.conf` contains your VPN password in a form like `XOR:1d1a0b`. That is not
encryption. Each byte is XORed with the constant 85 and hex-encoded, a keyless and symmetric
transform, so anyone able to read the file can trivially recover the password. The file's `0600`
root ownership is the only thing actually protecting it.

YAKG does not change this — it is how the Kerio client works, and YAKG must write a
configuration the client can read. What YAKG will do, from Milestone 4 onward, is keep its own
copy of your credentials in the desktop secret store (GNOME Keyring via the Secret Service API)
rather than in its own configuration files, and treat `/etc/kerio-kvc.conf` as the
credential-bearing file it is.

YAKG never reads that file during detection. Presence is established with a stat.

## Development

```bash
sudo apt install python3-venv python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
git clone https://github.com/alirezaazadbakht/yakg.git
cd yakg
python3 -m venv --system-site-packages .venv   # for the system GTK bindings
source .venv/bin/activate
pip install -e ".[dev]"

pytest        # tests
ruff check .  # lint
ruff format . # format
mypy          # types
```

The test suite needs no root, no VPN connection and no desktop session. Every system
interaction is faked, so the tests never touch the machine they run on. CI deliberately runs
without GTK installed, which enforces the rule that the backend stays importable without a
desktop stack.

### Architecture

```text
src/yakg/
├── main.py            entry point, --detect and --version
├── app.py             GTK application, actions, accelerators
├── ui/                widgets; never runs a system command
├── backend/
│   ├── process.py     the only place a subprocess is spawned
│   ├── constants.py   verified Kerio paths and names
│   ├── detect.py      is the client installed?
│   ├── service.py     systemd and SysV backends
│   └── status.py      assembles one snapshot for the UI
├── models/            frozen dataclasses shared by both sides
└── utils/             logging and redaction
```

The backend has no GTK dependency and is usable on its own, which is what makes `--detect` and
the test suite possible. The interface never runs a system command; it calls the backend and
updates itself when the answer arrives.

Detection runs on a worker thread and returns to the main loop through `GLib.idle_add`, so a slow
`systemctl` call cannot freeze the window.

Two conventions are worth knowing before contributing. State that cannot be determined is
reported as unknown rather than guessed: `active` and `enabled_at_boot` are tri-state, and a
timed-out probe produces "Unknown", never "Stopped". And facts about Kerio get verified and
written down in [docs/kerio-facts.md](docs/kerio-facts.md) before code depends on them.

## Troubleshooting

**"Kerio VPN Client not found"** — YAKG looks for the `kerio-control-vpnclient` package and for
a `kvpncsvc` executable in `/usr/sbin`, `/usr/local/sbin` and `/sbin`. Run `yakg --detect` to see
what it found. A package that was removed but not purged is correctly reported as not installed.

**The service shows "Unknown"** — YAKG could not determine the state, which is different from
knowing it is stopped. Open *Technical details* for the raw command output. The usual cause is
systemd being installed but not running as PID 1, which happens in containers and WSL.

**Connection state is "Unknown" even though I am connected** — expected in this release.
Determining a live tunnel needs facts that have not been verified yet; see the unresolved section
of [docs/kerio-facts.md](docs/kerio-facts.md). YAKG will report "Disconnected" only when it is
certain, which is when the service is stopped.

**The window will not start** — run `yakg` from a terminal. Missing GTK bindings produce an
explanation and the apt command that fixes it.

## Known limitations

- Read-only. Nothing can be started, stopped or configured from YAKG yet.
- Connection state is only reported when the service is stopped; otherwise it is "Unknown".
- No profiles, no tray icon, no notifications.
- Verified against exactly one Kerio version, on Ubuntu 24.04.
- No packaging: source installation only.

## Contributing

Issues and pull requests are welcome. A few expectations:

- Do not guess at Kerio behaviour. Verify it against an installation or reliable documentation,
  and record it in [docs/kerio-facts.md](docs/kerio-facts.md). An honest "unresolved" is much
  better than an invented detail.
- Keep system interaction out of the UI, and keep the backend importable without GTK.
- Add a test with a bug fix when it is practical.
- Run `pytest`, `ruff check .` and `mypy` before opening a pull request.
- Never commit credentials, real configuration files, or anything belonging to Kerio or GFI.

## License

MIT. See [LICENSE](LICENSE).

YAKG contains no Kerio or GFI code, binaries, logos or other assets. "Kerio" and "Kerio Control"
are trademarks of their respective owners and are used here only to describe what this program
interoperates with.
