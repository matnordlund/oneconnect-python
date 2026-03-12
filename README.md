# OneConnect Python Wrapper

Clavister NetWall OIDC + OpenConnect helper with a reusable core and GTK4 UI for Linux.

## Installation

**From source (recommended for development):**

```bash
git clone https://github.com/YOUR_USERNAME/oneconnect-python.git
cd oneconnect-python
pip install -e .
```

After install you can run:

- `oneconnect` — CLI (list, add-profile, connect, disconnect)
- `oneconnect-gui` — GTK profile picker and connection UI

**Run without installing** (from repo root):

```bash
python3 oneconnect_cli.py list
python3 -m oneconnect_gui.app   # GUI
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
- connect / disconnect with reliable pkexec-based process handling
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

## Notes

- This starter has been improved based on live Debian testing, but it is still a starter project rather than a packaged product.
- The next logical steps are secure secret storage, a polished profile editor, and later NetworkManager integration.

## License

MIT — see [LICENSE](LICENSE).
