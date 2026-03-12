from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import subprocess
import time
from typing import Optional


@dataclass(slots=True)
class AVStatus:
    enabled: bool
    updated: bool
    detail: str = ""


_TRUE = {"true", "yes", "1", "enabled"}
_FALSE = {"false", "no", "0", "disabled"}


def _parse_bool(value: str) -> Optional[bool]:
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return None


def check_av_auto() -> AVStatus:
    """Best-effort Linux-friendly AV check.

    This is intentionally heuristic. There is no universal Linux AV API.
    We primarily check common ClamAV markers.
    """
    freshclam_log = Path("/var/log/freshclam.log")
    clamdb = Path("/var/lib/clamav")
    clamd_sock_candidates = [
        Path("/run/clamav/clamd.ctl"),
        Path("/var/run/clamav/clamd.ctl"),
    ]

    enabled = any(p.exists() for p in clamd_sock_candidates) or any(
        (clamdb / name).exists() for name in ("main.cvd", "main.cld", "daily.cvd", "daily.cld")
    )

    updated = False
    newest_ts = 0.0
    for child in clamdb.glob("*"):
        try:
            newest_ts = max(newest_ts, child.stat().st_mtime)
        except OSError:
            pass
    if freshclam_log.exists():
        try:
            newest_ts = max(newest_ts, freshclam_log.stat().st_mtime)
        except OSError:
            pass
    if newest_ts:
        updated = (time.time() - newest_ts) < 14 * 24 * 3600

    detail = "auto-detected ClamAV-like state" if enabled else "no AV indicators found"
    return AVStatus(enabled=enabled, updated=updated if enabled else False, detail=detail)


def run_av_script(script_path: str) -> AVStatus:
    proc = subprocess.run([script_path], capture_output=True, text=True, timeout=15, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"AV script failed with exit code {proc.returncode}: {proc.stderr.strip()}")

    out = proc.stdout.strip()
    if not out:
        raise RuntimeError("AV script returned no output")

    simple = _parse_bool(out)
    if simple is not None:
        return AVStatus(enabled=simple, updated=simple, detail="script simple boolean")

    enabled = None
    updated = None
    for line in re.split(r"[\r\n ]+", out):
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        parsed = _parse_bool(value)
        if parsed is None:
            continue
        if key == "enabled":
            enabled = parsed
        elif key == "updated":
            updated = parsed

    if enabled is None or updated is None:
        raise RuntimeError(f"Could not parse AV script output: {out}")
    return AVStatus(enabled=enabled, updated=updated, detail="script key/value")
