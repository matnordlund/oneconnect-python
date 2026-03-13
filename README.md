# OneConnect Python Wrapper

Clavister NetWall OIDC + OpenConnect helper with a reusable core and GTK4 UI for Linux.

## Installation

**From source using a virtual environment** (recommended on managed Linux, e.g. Debian/Ubuntu, where system Python is externally managed):

```bash
git clone https://github.com/YOUR_USERNAME/oneconnect-python.git
cd oneconnect-python
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Then run the CLI or GUI with the venv active:

- `oneconnect` — CLI (list, add-profile, connect, disconnect)
- `oneconnect-gui` — GTK profile picker and connection UI

To leave the venv: `deactivate`. To use the app again later: `cd oneconnect-python && source .venv/bin/activate`, then `oneconnect` or `oneconnect-gui`.

**From source without venv** (when your system allows it):

```bash
git clone https://github.com/YOUR_USERNAME/oneconnect-python.git
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
  Pip is timing out when talking to PyPI. Try:
  - Increase timeout: `pip install --timeout 120 -e .`
  - Retry when the network is stable; if you use a VPN or proxy, ensure it allows access to pypi.org.
  - If PyPI is unreachable but the venv already has setuptools and wheel, install without fetching build deps:
    ```bash
    pip install setuptools wheel   # if not already installed
    pip install --no-build-isolation -e .
    ```

## Project layout

This project consists of:

- `oneconnect_core`: reusable auth, profile, AV, and OpenConnect launch logic
- `oneconnect_gui`: a GTK4/libadwaita profile picker and connection UI
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

The GTK UI includes:

- profile picker sidebar with libadwaita navigation styling
- add / edit / delete profiles
- modern profile editor using `Adw.PreferencesGroup` and `Adw.EntryRow`
- AV mode selection (`auto`, `script`, `manual`) and AV script path field
- advanced OpenConnect fields (server certificate pin, user-agent, OS, extra args)
- status pill that reflects Disconnected / Authenticating / Connecting / Connected / Error
- connect / disconnect with reliable pkexec-based process handling (direct or NetworkManager)
- “Use NM” switch to run the VPN via NetworkManager when available
- log output pane with auto-scroll and a Clear button
- toast notifications for profile actions and errors

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

Disconnect:

```bash
oneconnect disconnect Demo
```

## NetworkManager backend

You can run the VPN via **NetworkManager** instead of launching OpenConnect directly. The same OIDC flow and cookie are used; only the tunnel is started by NM’s openconnect plugin.

**Enable:**

- **CLI:** pass `--nm` (or `--network-manager`) to `connect` / `disconnect`, or set the default via config/env.
- **GUI:** turn on the “Use NM” switch in the header bar.
- **Config:** create `~/.config/oneconnect/config.json` with `{"use_networkmanager": true}`.
- **Env:** set `ONECONNECT_USE_NM=1` (overrides config file).

**Requirements:** `nmcli` and the openconnect VPN plugin (e.g. `network-manager-openconnect` or `NetworkManager-openconnect` on your distro). The plugin must be installed so that `nmcli connection add type vpn` can create an openconnect connection.

**CLI with NM:**

```bash
oneconnect connect Demo --nm
oneconnect disconnect Demo --nm
```

## Dependencies

Core runtime:

- Python 3.11+
- `aiohttp`
- `PyJWT`

GUI runtime:

- GTK4
- libadwaita 1
- PyGObject

OpenConnect runtime:

- `openconnect`
- `pkexec` recommended

When using the NetworkManager backend:

- NetworkManager with `nmcli`
- openconnect VPN plugin (e.g. `network-manager-openconnect`)

## Notes

- This starter has been improved based on live Debian testing, but it is still a starter project rather than a packaged product.
- NetworkManager integration is implemented as an optional backend (CLI `--nm`, GUI “Use NM” switch, config/env).

## License

MIT — see [LICENSE](LICENSE).
