"""Network helpers shared by the app and the scripts."""
from __future__ import annotations

import sys

_TLS_READY = False


def setup_tls() -> None:
    """Use the Windows certificate store for all HTTPS/WSS connections.

    Something on this PC inspects HTTPS traffic and re-signs it with a root certificate that
    Windows trusts but Python's bundled list (certifi) does not. `truststore` makes Python use
    the operating system's trust store instead, so connections verify correctly (verification
    stays ON; nothing is disabled).
    """
    global _TLS_READY
    if _TLS_READY:
        return
    import truststore

    truststore.inject_into_ssl()
    _TLS_READY = True


def utf8_console() -> None:
    """Make print() safe for non-ASCII text in the Windows console / redirected output."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


USER_AGENT = "MarketsLearningWorkbench/0.1 (personal educational app)"
