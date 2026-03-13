"""Global app config (e.g. use_networkmanager)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .profiles import CONFIG_DIR

CONFIG_FILE = CONFIG_DIR / "config.json"


def get_use_networkmanager() -> bool:
    """
    Return True if the NetworkManager backend should be used.
    Checked in order: env ONECONNECT_USE_NM, then config file use_networkmanager.
    """
    env = os.environ.get("ONECONNECT_USE_NM", "").strip().lower()
    if env in ("1", "true", "yes"):
        return True
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data.get("use_networkmanager"), bool):
                return data["use_networkmanager"]
        except (OSError, json.JSONDecodeError):
            pass
    return False


def set_use_networkmanager(value: bool) -> None:
    """Persist use_networkmanager to config file (for GUI toggle)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    if not isinstance(data, dict):
        data = {}
    data["use_networkmanager"] = value
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
