# OneConnect Python Wrapper

Clavister NetWall OIDC + OpenConnect helper with a reusable core and a systray GUI for Ubuntu/Linux.

## Installation

**From source using a virtual environment** (recommended on managed Linux, e.g. Debian/Ubuntu, where system Python is externally managed):

```bash
git clone https://github.com/matnordlund/oneconnect-python.git
cd oneconnect-python
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Then run the CLI or GUI with the venv active:

- `oneconnect` — CLI (list, add-profile, connect, disconnect, status)
- `oneconnect-gui` — systray icon and profile manager (GTK3, Yaru theme)

To leave the venv: `deactivate`. To use the app again later: `cd oneconnect-python && source .venv/bin/activate`, then `oneconnect` or `oneconnect-gui`.

**From source without venv** (when your system allows it):

```bash
git clone https://github.com/matnordlund/oneconnect-python.git
cd oneconnect-python
pip install -e .
```

**Run without installing** (from repo root):

```bash
python3 oneconnect_cli.py list
python3 -m oneconnect_gui.app   # GUI
```

### Install troubleshooting

- **ReadTimeoutError / "No matching distribution found" for setuptools**  
  By default, pip uses *build isolation*: it downloads setuptools and wheel from PyPI into a temporary environment and does not use your system (or venv) setuptools. If PyPI is slow or unreachable, use one of these:

  - **Use system setuptools (e.g. Debian/Ubuntu with `python3-setuptools` installed):** create the venv with access to system site-packages, then install without build isolation so the build uses the system setuptools and wheel:
    ```bash
    python3 -m venv .venv --system-site-packages
    source .venv/bin/activate
    pip install --no-build-isolation -e .
    ```
  - Increase timeout and retry: `pip install --timeout 120 -e .`
  - If the venv already has setuptools and wheel (e.g. you installed them earlier), run: `pip install --no-build-isolation -e .`

## Project layout

This project consists of:

- `oneconnect_core`: reusable auth, profile, AV, and OpenConnect launch logic
- `oneconnect_gui`: a GTK3 systray app and profile manager (Ubuntu/Yaru)
- `oneconnect_cli.py`: a simple CLI for testing and automation

## Highlights

- Uses the OpenConnect CLI client for the tunnel itself
- Uses system-browser OIDC with a loopback callback
- Normalizes hostnames like `vpn.example.com` to `https://vpn.example.com`
- Detects OpenConnect version from the installed binary
- Builds `ClientEnvironment` from the Linux host:
  - `ClientVersion`: `openconnect --version` without leading `v`
  - `OperatingSystemArchitecture`: `uname -m`
  - `OperatingSystemInformation`: `/etc/os-release` `PRETTY_NAME`, else `uname -o`
- Supports `pkexec` for privileged OpenConnect launch/disconnect
- When using pkexec (default), the direct backend runs OpenConnect with `--background`, writes a per-profile PID file under `~/.config/oneconnect/` (e.g. `openconnect-Demo.pid`), and uses `--setuid` so the daemon runs as the user who invoked the CLI; connection output is appended to `openconnect-<profile>.log` in the same directory; disconnect uses the PID file to terminate the correct process and does not require pkexec (the daemon runs as your user)

## Profile storage

Profiles are stored in:

`~/.config/oneconnect/profiles.json`

Each profile supports:

- NetWall server URI
- optional server certificate pin
- OpenConnect user-agent and `--os`
- extra OpenConnect arguments
- AV mode and AV script path

## AV / posture handling

There is no universal Linux desktop AV API, so this app supports three modes:

- `auto`: heuristic checks for ClamAV-like state
- `script`: run a script and parse its result
- `manual`: fixed values stored per profile

### Script contract

Your AV script may output either:

```text
TRUE
```

or

```text
FALSE
```

or

```text
enabled=TRUE updated=TRUE
```

The script is expected to return exit code `0` on success.

## GUI features

The GUI is systray-first and uses the system theme (e.g. Yaru on Ubuntu, including dark/light):

- **System tray (Ayatana AppIndicator):** One icon in the panel; menu lists all configured profiles. Select “Connect to” → &lt;profile&gt; to connect. When connected, the icon changes, and the menu shows “Connected: &lt;name&gt;”, “Disconnect”, and “View log” (opens the tunnel log file in your default editor).
- **Profile manager:** Open “Manage profiles” from the tray to add, edit, and delete profiles (name, NetWall server URI, username, device seed). If you start the GUI with no profiles, the manager window opens so you can add one.
- **Dependencies:** GTK3 and Ayatana AppIndicator. On Ubuntu: `apt install gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1` (and the matching libraries, e.g. `libayatana-appindicator3-1`). The GUI uses the direct OpenConnect backend only (no NetworkManager option in the tray).

## CLI examples

Add a profile:

```bash
oneconnect add-profile --name Demo --server-uri sg.demo.clavister.com
# or from source: python3 oneconnect_cli.py add-profile --name Demo --server-uri sg.demo.clavister.com
```

Connect using `pkexec` by default:

```bash
oneconnect connect Demo
```

Connect without `pkexec`:

```bash
oneconnect connect Demo --no-pkexec
```

Status (direct backend: checks pid file and parses log for connection IP; omit profile to show all):

```bash
oneconnect status
oneconnect status Demo
```

Disconnect (omit profile to disconnect all connected profiles):

```bash
oneconnect disconnect
oneconnect disconnect Demo
```

## NetworkManager backend *(experimental, mostly broken)*

**Warning:** The NetworkManager integration is experimental and often fails in practice (e.g. “Connection activation failed: Unknown reason” with current NM-openconnect on many distros). Prefer the default direct OpenConnect backend for reliable use.

You can run the VPN via **NetworkManager** instead of launching OpenConnect directly. The same OIDC flow and cookie are used; only the tunnel is started by NM’s openconnect plugin.

**Enable:**

- **CLI:** pass `--nm` (or `--network-manager`) to `connect` / `disconnect`, or set the default via config/env.
- **Config:** create `~/.config/oneconnect/config.json` with `{"use_networkmanager": true}`.
- **Env:** set `ONECONNECT_USE_NM=1` (overrides config file).

**Requirements:** `nmcli` and the openconnect VPN plugin (e.g. `network-manager-openconnect` or `NetworkManager-openconnect` on your distro). The plugin must be installed so that `nmcli connection add type vpn` can create an openconnect connection.

**CLI with NM:**

```bash
oneconnect connect Demo --nm
oneconnect disconnect Demo --nm
```

**If you see "No valid secrets":** (1) On failure, the passwd-file path is logged—inspect it with `cat /tmp/tmpXXXX.txt`. (2) See which secrets the plugin expects by running `nmcli connection up oneconnect-<name> --ask` and noting the prompt labels (Cookie, Gateway, etc.). (3) For more detail, run with `NM_DEBUG=debug` and check `journalctl -u NetworkManager`.

Internally, when `--nm` is enabled, OneConnect now performs a short TLS probe after the OIDC/NetWall bootstrap to discover the final AnyConnect connect URL and the gateway certificate fingerprint. These values are fed into NetworkManager as:

- `vpn.secrets.cookie` – the `webvpn=...` cookie returned by NetWall.
- `vpn.secrets.gateway` – the final connect URL (after any redirects).
- `vpn.secrets.gwcert` – the TLS certificate fingerprint, which NM-openconnect passes to `openconnect` as `--servercert`.

If the probe cannot obtain a fingerprint (for example due to TLS or connectivity issues), the CLI automatically falls back to launching `openconnect` directly instead of NetworkManager, so `oneconnect connect Demo` continues to work even when `oneconnect connect Demo --nm` cannot be satisfied by the local NM/openconnect stack.

## Dependencies

Core runtime:

- Python 3.11+
- `aiohttp`
- `PyJWT`

GUI runtime (Ubuntu):

- GTK3 and Ayatana AppIndicator (e.g. `gir1.2-gtk-3.0`, `gir1.2-ayatanaappindicator3-0.1`, `libayatana-appindicator3-1`)
- PyGObject (`python3-gi`)

OpenConnect runtime:

- `openconnect`
- `pkexec` recommended

When using the NetworkManager backend:

- NetworkManager with `nmcli`
- openconnect VPN plugin (e.g. `network-manager-openconnect`)

## Notes

- This starter has been improved based on live Debian testing, but it is still a starter project rather than a packaged product.
- NetworkManager integration is implemented as an optional, experimental backend (CLI `--nm`, config/env); it is mostly broken on many setups—use direct OpenConnect for reliability.

## License

MIT — see [LICENSE](LICENSE).
