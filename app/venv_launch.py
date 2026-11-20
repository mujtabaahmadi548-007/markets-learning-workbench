"""Make `python run.py` (or `python scripts\\x.py`) work even when typed with the global Python.

If the current interpreter is not the project's .venv, re-run the same command with
.venv\\Scripts\\python.exe and exit with its exit code. Standard library only (this runs before
any third-party package is importable).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = PROJECT_ROOT / ".venv"
VENV_PYTHON = VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_venv() -> None:
    if os.environ.get("MLW_IN_VENV_RELAUNCH") == "1":
        return
    try:
        inside = Path(sys.prefix).resolve() == VENV_DIR.resolve()
    except OSError:
        inside = False
    if inside:
        return
    if not VENV_PYTHON.exists():
        print("\nThe project's virtual environment (.venv) was not found.")
        print("See README.md -> 'Setup' to create it (one step at a time).\n")
        sys.exit(1)
    env = {**os.environ, "MLW_IN_VENV_RELAUNCH": "1"}
    try:
        code = subprocess.call([str(VENV_PYTHON), *sys.argv], env=env)
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)
