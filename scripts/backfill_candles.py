r"""Download chart history (real candles) for every enabled asset and store it in the database.

Run from the project folder (the app may be running or not):
    python scripts\backfill_candles.py
    python scripts\backfill_candles.py --ranges 6M 1Y      # only some ranges

The app also does this by itself (on page views and every 6 hours); this script just fills everything at once.
Note: with a Twelve Data key, each stock and range uses about 1 credit (of 800/day).
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import asyncio
import sys

from app.config import load_settings
from app.db.migrations import init_db
from app.db.session import Database
from app.providers.base import make_http_client
from app.providers.registry import build_providers
from app.services.assets import load_assets, seed_assets
from app.services.candles import RANGES, CandleService
from app.services.provider_health import ProviderHealth


async def main_async(ranges: list[str], symbols: list[str] | None) -> int:
    settings = load_settings()
    db = Database(settings.database_path)
    init_db(db)
    seed_assets(db)
    assets = [a for a in load_assets(db) if a.enabled and (not symbols or a.symbol in symbols)]
    failures = 0
    async with make_http_client() as http:
        providers = build_providers(settings, http)
        health = {n: ProviderHealth(name=n, label=p.label, configured=p.configured) for n, p in providers.items()}
        svc = CandleService(db, providers, health)
        print(f"\n{'ASSET':9} {'RANGE':5} {'RESULT':6} DETAIL")
        for a in assets:
            for rng in ranges:
                chart = await svc.chart(a, rng)
                n = len(chart.get("candles", []))
                if n:
                    flag = " (proxy)" if chart.get("is_proxy") else ""
                    print(f"{a.symbol:9} {rng:5} OK     {n:4} {chart['timeframe']} candles from {chart['source']}{flag}")
                else:
                    failures += 1
                    print(f"{a.symbol:9} {rng:5} FAIL   {'; '.join(chart.get('problems', []))[:100]}")
    print(f"\nDone. {failures} failures.")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ranges", nargs="*", default=list(RANGES), choices=list(RANGES))
    parser.add_argument("--symbols", nargs="*", help="only these assets, e.g. --symbols TSLA BTC")
    args = parser.parse_args()
    return asyncio.run(main_async(args.ranges, [s.upper() for s in args.symbols] if args.symbols else None))


if __name__ == "__main__":
    sys.exit(main())
