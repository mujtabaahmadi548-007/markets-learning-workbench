r"""Start the Markets Learning Workbench.

    python run.py            # start the server (automatically uses .venv)
    python run.py --open     # start and open the browser

Binds to 127.0.0.1 (this PC only). Set LAN_ACCESS=true in .env to open it from your phone
on the same Wi-Fi. Set PORT= in .env to change the port (default 8000).
"""
from __future__ import annotations

from app.venv_launch import ensure_venv

ensure_venv()  # if started with the global Python, re-run with .venv\Scripts\python.exe

import argparse  # noqa: E402
import socket  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import webbrowser  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.net import setup_tls, utf8_console  # noqa: E402


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def lan_ip() -> str | None:
    """Best guess at this PC's Wi-Fi/LAN address (no traffic is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def main() -> int:
    utf8_console()
    parser = argparse.ArgumentParser(description="Markets Learning Workbench")
    parser.add_argument("--open", action="store_true", help="open the app in your browser")
    args = parser.parse_args()

    setup_tls()
    settings = get_settings()
    host, port = settings.host, settings.port

    if not port_is_free(host, port):
        print(f"\nPort {port} is already in use (maybe the app, or your AI Research Workbench, is already running).")
        print(f"Either close the other program, or set a different port in .env, for example: PORT={port + 1}\n")
        return 1

    local_url = f"http://127.0.0.1:{port}"
    print("\nMarkets Learning Workbench")
    print(f"  Open in your browser:  {local_url}")
    if settings.lan_access:
        ip = lan_ip()
        print(f"  On your phone (same Wi-Fi):  http://{ip or '<this-PC-IP>'}:{port}")
        print("  If Windows asks about the firewall, allow 'Private networks' only.")
    print("  Stop the server: press Ctrl+C in this window.\n")

    if args.open:
        threading.Timer(1.5, lambda: webbrowser.open(local_url)).start()

    import uvicorn

    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=host,
        port=port,
        log_config=None,  # use the app's own logging (logs/app.log, secrets redacted)
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
