"""Runs the browser-side unit tests (tests/js/*.test.js) with Node, so `pytest` covers them too.
Skipped when Node.js is not installed."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_browser_market_status_badges() -> None:
    files = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.js"))
    r = subprocess.run(["node", "--test", *files], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
