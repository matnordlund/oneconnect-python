from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Callable, Optional

from .profiles import CONFIG_DIR, Profile


class OpenConnectLaunchError(RuntimeError):
    pass


def get_openconnect_pid_file_path(profile: Profile) -> Path:
    """Stable path for the openconnect daemon PID file for this profile."""
    name = (profile.name or "").strip()
    if name:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-")
        if slug:
            return CONFIG_DIR / f"openconnect-{slug[:64]}.pid"
    return CONFIG_DIR / f"openconnect-{profile.id[:12]}.pid"


def _find_openconnect() -> str | None:
    search_path = os.environ.get("PATH", "")
    extra_dirs = ["/usr/sbin", "/usr/local/sbin", "/sbin"]
    merged_path = os.pathsep.join([p for p in [search_path, *extra_dirs] if p])

    exe = shutil.which("openconnect", path=merged_path)
    if exe:
        return exe

    for candidate in (
        "/usr/sbin/openconnect",
        "/usr/local/sbin/openconnect",
        "/sbin/openconnect",
    ):
        if Path(candidate).exists() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _find_pkexec() -> str | None:
    return shutil.which("pkexec")


def _find_kill() -> str:
    return shutil.which("kill") or "/bin/kill"


def _find_pkill() -> str:
    return shutil.which("pkill") or "/usr/bin/pkill"


def _find_mkdir() -> str:
    return shutil.which("mkdir") or "/bin/mkdir"


def _shell_quote(value: str) -> str:
    return shlex.quote(value)


def _build_match_pattern(profile: Profile, exe: str) -> str:
    server = profile.openconnect_server or profile.server_uri
    return f"^{exe} {server} .*--useragent={profile.useragent} .*--os={profile.vpn_os}( |$)"


async def disconnect_openconnect(
    root_pid: int | None,
    profile: Profile | None = None,
    pid_file: Optional[Path] = None,
    log: Optional[Callable[[str], None]] = None,
    use_pkexec: bool = True,
) -> int:
    log = log or (lambda msg: None)

    pid_to_kill: int | None = root_pid
    if pid_to_kill is None and profile is not None:
        path = pid_file if pid_file is not None else get_openconnect_pid_file_path(profile)
        if path.exists():
            try:
                pid_to_kill = int(path.read_text().strip())
            except (ValueError, OSError):
                pid_to_kill = None
            if pid_to_kill is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    if pid_to_kill is not None:
        base_cmd = [_find_kill(), "-TERM", str(pid_to_kill)]
    else:
        if not profile:
            raise OpenConnectLaunchError("No active OpenConnect PID is available for disconnect")
        exe = _find_openconnect()
        if not exe:
            raise OpenConnectLaunchError("openconnect executable was not found for disconnect fallback")
        pattern = _build_match_pattern(profile, exe)
        base_cmd = [_find_pkill(), "-TERM", "-f", pattern]

    cmd = base_cmd
    if use_pkexec:
        pkexec = _find_pkexec()
        if not pkexec:
            raise OpenConnectLaunchError("pkexec requested for disconnect but not found in PATH")
        cmd = [pkexec, *base_cmd]

    log("Disconnecting: " + " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    async for line in proc.stdout:
        log(line.decode("utf-8", errors="replace").rstrip())
    return await proc.wait()


def _current_username() -> str:
    """Return the username of the current (invoking) user for --setuid."""
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError):
        return os.environ.get("USER", "nobody")


async def run_openconnect(
    profile: Profile,
    cookie: str,
    log: Optional[Callable[[str], None]] = None,
    use_pkexec: bool = False,
    proc_holder: object | None = None,
) -> int:
    log = log or (lambda msg: None)
    server = profile.openconnect_server or profile.server_uri
    exe = _find_openconnect()
    if not exe:
        raise OpenConnectLaunchError(
            "openconnect executable was not found in PATH or common sbin locations"
        )

    base_args = [
        exe,
        server,
        "--cookie-on-stdin",
        f"--useragent={profile.useragent}",
        f"--os={profile.vpn_os}",
    ]
    if profile.servercert:
        base_args.append(f"--servercert={profile.servercert}")
    base_args.extend(profile.extra_openconnect_args)

    pid_file_path: Path | None = None
    if use_pkexec:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        pid_file_path = get_openconnect_pid_file_path(profile)
        base_args.append("--background")
        base_args.append(f"--pid-file={pid_file_path}")
        base_args.append(f"--setuid={_current_username()}")

    if use_pkexec:
        pkexec = _find_pkexec()
        if not pkexec:
            raise OpenConnectLaunchError("pkexec requested but not found in PATH")
        mkdir_bin = _find_mkdir()
        quoted_base = " ".join(_shell_quote(arg) for arg in base_args)
        # Redirect stdout/stderr so the backgrounded daemon doesn't keep our pipe open
        shell_cmd = f"exec {quoted_base} >/dev/null 2>&1"
        cmd = [pkexec, "/bin/sh", "-c", f"{_shell_quote(mkdir_bin)} -p /var/run/vpnc && {shell_cmd}"]
    else:
        cmd = base_args

    log("Launching: " + " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    if proc_holder is not None:
        try:
            proc_holder.current_proc = proc
            proc_holder.root_pid = proc.pid
        except Exception:
            pass

    assert proc.stdin is not None
    proc.stdin.write(cookie.encode("utf-8") + b"\n")
    await proc.stdin.drain()
    proc.stdin.close()

    assert proc.stdout is not None
    async for line in proc.stdout:
        log(line.decode("utf-8", errors="replace").rstrip())
    rc = await proc.wait()
    if proc_holder is not None:
        try:
            proc_holder.current_proc = None
            if pid_file_path is not None and pid_file_path.exists():
                try:
                    proc_holder.root_pid = int(pid_file_path.read_text().strip())
                except (ValueError, OSError):
                    proc_holder.root_pid = None
            else:
                proc_holder.root_pid = None
        except Exception:
            pass
    return rc
