from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import platform
import re
import shutil
import subprocess
from typing import Optional

from .avcheck import AVStatus, check_av_auto, run_av_script
from .configauthxml import ClientEnvironment
from .profiles import AVConfig


def _find_executable(name: str) -> Optional[str]:
    exe = shutil.which(name)
    if exe:
        return exe
    for candidate in (f"/usr/sbin/{name}", f"/usr/local/sbin/{name}", f"/sbin/{name}"):
        if Path(candidate).exists() and os.access(candidate, os.X_OK):
            return candidate
    return None


def compute_uid(username: str, seed: Optional[str] = None, spoof_uid_hex: Optional[str] = None) -> str:
    if spoof_uid_hex:
        return spoof_uid_hex.lower()
    system_id_bytes = hashlib.sha256((seed or "oneconnect-default").encode("utf-8")).digest()
    h = hashlib.sha256()
    h.update(system_id_bytes)
    h.update(username.encode("utf-8"))
    return h.hexdigest()


def get_openconnect_version() -> str:
    exe = _find_executable("openconnect")
    if not exe:
        return "unknown"
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5, check=False)
        first_line = (proc.stdout or proc.stderr).splitlines()[0]
        m = re.search(r"openconnect version\s+([^\s]+)", first_line, flags=re.I)
        if m:
            return m.group(1).lstrip("vV")
        return first_line.strip() or "unknown"
    except Exception:
        return "unknown"


def get_os_architecture() -> str:
    return platform.machine() or "unknown"


def get_os_information() -> str:
    os_release = Path("/etc/os-release")
    if os_release.exists():
        try:
            for line in os_release.read_text(encoding="utf-8").splitlines():
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
    try:
        proc = subprocess.run(["uname", "-o"], capture_output=True, text=True, timeout=5, check=False)
        result = proc.stdout.strip()
        if result:
            return result
    except Exception:
        pass
    return "Linux"


def resolve_av_status(config: AVConfig) -> AVStatus:
    if config.mode == "auto":
        return check_av_auto()
    if config.mode == "script":
        if not config.script_path:
            raise RuntimeError("AV mode is 'script' but no script path is configured")
        return run_av_script(config.script_path)
    return AVStatus(enabled=config.manual_enabled, updated=config.manual_updated, detail="manual")


def build_client_environment(username: str, seed: Optional[str], av_config: AVConfig, spoof_uid_hex: Optional[str] = None) -> ClientEnvironment:
    av = resolve_av_status(av_config)
    return ClientEnvironment(
        uid=compute_uid(username=username, seed=seed, spoof_uid_hex=spoof_uid_hex),
        client_version=get_openconnect_version(),
        wolfssl_version=None,
        operating_system_information=get_os_information(),
        operating_system_architecture=get_os_architecture(),
        is_av_enabled=av.enabled,
        is_av_updated=av.updated,
    )
