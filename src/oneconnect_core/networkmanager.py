"""NetworkManager VPN (openconnect) backend: create/update connection, activate with secrets, deactivate."""
from __future__ import annotations

import asyncio
import json as _json
import os
import re
import tempfile
import time as _time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from .profiles import Profile

# #region agent log
_DBG_LOG = Path(tempfile.gettempdir()) / "oneconnect-debug-5d8d44.log"
def _dbg(hypothesis: str, location: str, message: str, data: dict | None = None) -> None:
    try:
        with open(_DBG_LOG, "a") as _f:
            _f.write(_json.dumps({"sessionId": "5d8d44", "hypothesisId": hypothesis, "location": f"networkmanager.py:{location}", "message": message, "data": data or {}, "timestamp": int(_time.time() * 1000)}) + "\n")
    except Exception:
        pass
# #endregion


CONNECTION_ID_PREFIX = "oneconnect-"
NMCLI = "nmcli"


class NetworkManagerError(RuntimeError):
    """Raised when a NetworkManager / nmcli operation fails."""
    pass


def _gateway_from_profile(profile: Profile) -> str:
    """Return the OpenConnect gateway host (hostname:port or hostname) for NM."""
    server = profile.openconnect_server or profile.server_uri
    parsed = urlparse(server)
    host = (parsed.netloc or "").strip()
    if not host:
        raise ValueError(f"Invalid server URI for gateway: {server}")
    return host


def _connection_id_from_profile(profile: Profile) -> str:
    """Stable NM connection id for this profile (safe for nmcli)."""
    name = (profile.name or "").strip()
    if name:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-")
        if slug:
            return CONNECTION_ID_PREFIX + slug[:64]
    return CONNECTION_ID_PREFIX + profile.id[:12]


def _find_nmcli() -> str:
    import shutil
    exe = shutil.which(NMCLI)
    if exe:
        return exe
    for c in ("/usr/bin/nmcli", "/usr/local/bin/nmcli"):
        if Path(c).exists():
            return c
    return NMCLI


def is_networkmanager_available() -> bool:
    """Return True if nmcli appears to be available (NetworkManager running)."""
    import subprocess
    try:
        r = subprocess.run(
            [_find_nmcli(), "--version"],
            capture_output=True,
            timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


async def _run_nmcli(
    *args: str,
    log: Optional[Callable[[str], None]] = None,
) -> tuple[int, str, str]:
    exe = _find_nmcli()
    cmd = [exe] + list(args)
    log = log or (lambda _: None)
    log(f"Running: {' '.join(cmd)}")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    out_s = out.decode("utf-8", errors="replace")
    err_s = err.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        log(f"nmcli stderr: {err_s}")
    return proc.returncode, out_s, err_s


async def ensure_nm_connection(
    profile: Profile,
    log: Optional[Callable[[str], None]] = None,
) -> str:
    """
    Create or update a NetworkManager VPN (openconnect) connection for this profile.
    Returns the connection id (con-name) used by nmcli.
    """
    con_id = _connection_id_from_profile(profile)
    gateway = _gateway_from_profile(profile)

    rc, out, err = await _run_nmcli(
        "connection", "show", con_id,
        log=log,
    )
    # Flags=2 (NOT_SAVED): NM asks the secret agent each time; nmcli+passwd-file acts as agent.
    # gwcert-flags=4 (NOT_REQUIRED): gwcert is optional — only provided when profile has a cert pin.
    base_data = f"gateway={gateway},protocol=anyconnect,cookie-flags=2,gateway-flags=2,gwcert-flags=4"
    if profile.servercert:
        base_data += f",servercert={profile.servercert}"

    if rc == 0:
        rc2, _, _ = await _run_nmcli(
            "connection", "modify", con_id,
            "vpn.data", base_data,
            log=log,
        )
        if rc2 != 0:
            raise NetworkManagerError(f"Failed to update NM connection {con_id}: {err}")
        return con_id

    rc, _, err = await _run_nmcli(
        "connection", "add",
        "type", "vpn",
        "con-name", con_id,
        "vpn.service-type", "org.freedesktop.NetworkManager.openconnect",
        "vpn.data", base_data,
        "connection.autoconnect", "false",
        log=log,
    )
    if rc != 0:
        raise NetworkManagerError(f"Failed to add NM connection {con_id}: {err}")
    return con_id


async def activate_nm_connection(
    profile: Profile,
    cookie: str,
    log: Optional[Callable[[str], None]] = None,
) -> int:
    """
    Ensure the NM connection exists, then activate it with the given cookie (and gateway).
    Cookie is supplied via a temporary passwd-file; not stored in NM config.
    Returns exit code 0 on success.
    """
    con_id = await ensure_nm_connection(profile, log=log)
    gateway = _gateway_from_profile(profile)

    # #region agent log — H-C/H-D: dump connection profile to see actual vpn.data
    _rc_dump, dump_out, _ = await _run_nmcli("connection", "show", con_id, log=lambda _: None)
    vpn_data_lines = [l for l in dump_out.splitlines() if "vpn.data" in l.lower() or "vpn.secret" in l.lower() or "vpn.service" in l.lower()]
    _dbg("H-C", "activate:dump", "connection profile vpn fields after ensure", {"lines": vpn_data_lines})
    # #endregion

    # The NM openconnect plugin maps vpn.secrets.gwcert → --servercert.
    # Use profile.servercert when set; otherwise provide empty gwcert so the plugin
    # gets all three keys (avoids "failed to provide sufficient secrets" on final request).
    gwcert = profile.servercert or ""

    # #region agent log — H-I/H-J: log gwcert and profile details
    _dbg("H-I", "activate:secrets_info", "secrets being prepared", {
        "gateway": gateway,
        "gwcert": gwcert,
        "has_servercert": bool(profile.servercert),
        "cookie_prefix": cookie[:20] if cookie else "",
    })
    # #endregion

    passwd_path = None
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        delete=False,
        delete_on_close=False,
    ) as f:
        # Provide both vpn.secrets.* and vpn.secret.* (NM 1.12+ may use singular for final request).
        # Some plugin flows also request "password" (same as cookie for cookie-based auth).
        # All three main keys; gwcert empty when no cert pin (gwcert-flags=4).
        lines = [
            f"vpn.secrets.cookie:{cookie}",
            f"vpn.secrets.gateway:{gateway}",
            f"vpn.secrets.gwcert:{gwcert}",
            f"vpn.secrets.password:{cookie}",
            f"vpn.secret.cookie:{cookie}",
            f"vpn.secret.gateway:{gateway}",
            f"vpn.secret.gwcert:{gwcert}",
            f"vpn.secret.password:{cookie}",
        ]
        content = "\n".join(lines) + "\n"
        f.write(content)
        # #region agent log — H-I: log passwd-file keys
        _dbg("H-I", "activate:passwd", "passwd-file content", {"keys": [l.split(":")[0] for l in lines], "num_lines": len(lines)})
        # #endregion
        f.flush()
        os.fsync(f.fileno())
        passwd_path = f.name

    rc = -1
    try:
        log(f"Activating NM connection {con_id} (passwd-file: {passwd_path})")
        rc, out, err = await _run_nmcli(
            "connection", "up", con_id,
            "passwd-file", passwd_path,
            log=log,
        )
        # #region agent log — H-I: log result
        _dbg("H-I", "activate:up_result", "connection up result", {"rc": rc, "err": err.strip()})
        # #endregion
        if rc != 0:
            log(f"nmcli up failed: {err}")
            log(f"Passwd-file kept for debugging: {passwd_path}")
        return rc
    finally:
        if passwd_path and rc == 0:
            try:
                Path(passwd_path).unlink(missing_ok=True)
            except OSError:
                pass


async def deactivate_nm_connection(
    profile: Profile,
    log: Optional[Callable[[str], None]] = None,
) -> int:
    """
    Deactivate the NetworkManager VPN connection for this profile.
    Returns exit code 0 on success.
    """
    con_id = _connection_id_from_profile(profile)
    rc, out, err = await _run_nmcli(
        "connection", "down", con_id,
        log=log,
    )
    if rc != 0:
        log(f"nmcli down: {err}")
    return rc
