r"""List the Gemini models YOUR key can use, and recommend a fast and a smart one.

Run from the project folder:
    .\.venv\Scripts\python.exe scripts\list_gemini_models.py
    .\.venv\Scripts\python.exe scripts\list_gemini_models.py --probe       # also send 1 tiny test request per recommended model
    .\.venv\Scripts\python.exe scripts\list_gemini_models.py --probe --write-env   # save the working choices into .env

Listing models is free. --probe uses 1 request per model tested (2-3 total), counted in the daily limit.
The key is never printed (only its last 4 characters).
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (path + TLS setup)

import argparse
import re
import sys
import time

from app.ai.gemini_client import AIError, GeminiService, pick_recommended_models
from app.config import ENV_FILE, mask_secret, load_settings
from app.db.migrations import init_db
from app.db.session import Database


def update_env_models(fast: str, smart: str) -> None:
    """Set GEMINI_MODEL_FAST / GEMINI_MODEL_SMART in .env. Every other line (including your key) is left exactly as it is."""
    text = ENV_FILE.read_text(encoding="utf-8")
    for name, value in (("GEMINI_MODEL_FAST", fast), ("GEMINI_MODEL_SMART", smart)):
        pattern = re.compile(rf"^{name}=.*$", re.MULTILINE)
        if pattern.search(text):
            text = pattern.sub(f"{name}={value}", text)
        else:
            text = text.rstrip("\n") + f"\n{name}={value}\n"
    ENV_FILE.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true", help="send one tiny request to each recommended model")
    parser.add_argument("--write-env", action="store_true", help="save the working fast/smart models into .env (needs --probe)")
    parser.add_argument("--fast", help="try this model first for the fast role")
    parser.add_argument("--smart", help="try this model first for the smart role")
    args = parser.parse_args()

    settings = load_settings()
    print(f"\nGEMINI_API_KEY: {mask_secret(settings.gemini_api_key)}")
    if not settings.gemini_api_key:
        print("Open .env and paste your key after GEMINI_API_KEY= , save the file, then run this again.")
        return 1

    db = Database(settings.database_path)
    init_db(db)
    svc = GeminiService(settings, db)

    try:
        models = svc.list_models()
    except AIError as e:
        print(f"FAILED to list models: {e}")
        return 1
    except Exception as e:  # noqa: BLE001
        from app.logging_setup import redact

        print(f"FAILED to list models: {redact(str(e))[:400]}")
        return 1

    text_models = [m for m in models if "generateContent" in m["supported_actions"]]
    print(f"\nYour key can see {len(models)} models; {len(text_models)} can generate text (generateContent):\n")
    print(f"  {'MODEL ID':42} {'INPUT TOKENS':>12}  DISPLAY NAME")
    for m in sorted(text_models, key=lambda m: m["name"]):
        print(f"  {m['name']:42} {m['input_token_limit'] or 0:>12}  {m['display_name']}")

    rec = pick_recommended_models(models)
    print("\nRecommendation (stable text models only; preview/audio/image models skipped):")
    print(f"  Fast (bulk tagging):        {rec['fast'] or 'none found'}")
    print(f"  Smart (breakdowns):         {rec['smart'] or 'none found'}")
    if rec["pro"]:
        print(f"  Strongest 'pro' option:     {rec['pro']}  (free-tier limits for pro models are often much lower)")

    if not args.probe:
        print("\nNext: run again with --probe to check these models really answer with your key.")
        return 0

    # Try candidates in order; a busy model (503 "high demand" or no answer within 60 s) is skipped.
    print("\nProbing (1 tiny request per model tried; at most 3 per role):")
    tried: dict[str, bool] = {}

    def first_working(role: str, candidates: list[str]) -> str | None:
        for name in candidates[:3]:
            if name in tried:
                if tried[name]:
                    return name
                continue
            t = time.perf_counter()
            try:
                res = svc.generate("model_probe", "Reply with the single word: OK", model=name, temperature=0, max_retries=0)
                tried[name] = True
                print(f"  OK    {role:5} {name:28} {time.perf_counter() - t:5.1f}s  replied {res.text.strip()[:12]!r}")
                return name
            except AIError as e:
                tried[name] = False
                print(f"  BUSY  {role:5} {name:28} {time.perf_counter() - t:5.1f}s  {str(e)[:110]}")
        return None

    fast_list = ([args.fast] if args.fast else []) + [n for n in rec["fast_candidates"] if n != args.fast]
    smart_list = ([args.smart] if args.smart else []) + [n for n in rec["smart_candidates"] if n != args.smart]
    fast = first_working("fast", fast_list)
    smart = first_working("smart", smart_list)
    print(f"\nWorking now:  fast = {fast or 'none'}   smart = {smart or 'none'}")
    if args.write_env:
        if fast and smart:
            update_env_models(fast, smart)
            print(f"Saved to .env: GEMINI_MODEL_FAST={fast}  GEMINI_MODEL_SMART={smart}")
        else:
            print("Not saving to .env: no working model found for one role. Try again later.")
            return 1
    return 0 if fast and smart else 1


if __name__ == "__main__":
    sys.exit(main())
