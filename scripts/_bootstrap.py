"""Lets scripts in this folder import the `app` package. Import this first in every script."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.venv_launch import ensure_venv  # noqa: E402

ensure_venv()  # works with plain `python scripts\x.py` too

from app.net import setup_tls, utf8_console  # noqa: E402

utf8_console()
setup_tls()
