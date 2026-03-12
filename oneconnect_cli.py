#!/usr/bin/env python3
"""Launcher for development: run from repo root with python3 oneconnect_cli.py ..."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from oneconnect_core.cli import main

if __name__ == "__main__":
    main()
