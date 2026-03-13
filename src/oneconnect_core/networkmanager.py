"""NetworkManager VPN (openconnect) backend: create/update connection, activate with secrets, deactivate."""
from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from .profiles import Profile


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

    # vpn.data keys for NM openconnect plugin (gateway required; rest optional)
    vpn_data_parts = [f"gateway={gateway}"]
    if profile.servercert:
        vpn_data_parts.append(f"servercert={profile.servercert}")
    # Many NM openconnect plugins support cert, csd, proxy, etc.
    vpn_data = ",".join(vpn_data_parts)

    rc, out, err = await _run_nmcli(
        "connection", "show", con_id,
        log=log,
    )
    if rc == 0:
        # Exists: update gateway and vpn.data
        rc2, _, _ = await _run_nmcli(
            "connection", "modify", con_id,
            "vpn.data", vpn_data,
            log=log,
        )
        if rc2 != 0:
            raise NetworkManagerError(f"Failed to update NM connection {con_id}: {err}")
        return con_id

    # Add new connection: type vpn, openconnect
    rc, _, err = await _run_nmcli(
        "connection", "add",
        "type", "vpn",
        "con-name", con_id,
        "vpn.service-type", "org.freedesktop.NetworkManager.openconnect",
        "vpn.data", vpn_data,
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

    # NM openconnect plugin expects vpn.secrets.cookie and vpn.secrets.gateway
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        delete=False,
        delete_on_close=False,
    ) as f:
        f.write(f"vpn.secrets.cookie:{cookie}\n")
        f.write(f"vpn.secrets.gateway:{gateway}\n")
        path = f.name

    try:
        rc, out, err = await _run_nmcli(
            "connection", "up", con_id,
            "passwd-file", path,
            log=log,
        )
        if rc != 0:
            log(f"nmcli up failed: {err}")
        return rc
    finally:
        try:
            Path(path).unlink(missing_ok=True)
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
