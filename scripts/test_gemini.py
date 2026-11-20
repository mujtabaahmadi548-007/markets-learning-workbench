r"""Test the Gemini connection: one plain-text request and one structured-JSON request.

Run from the project folder:
    .\.venv\Scripts\python.exe scripts\test_gemini.py

Uses 2 requests (counted in the daily limit). The key is never printed (only its last 4 characters).
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (path + TLS setup)

import sys
import time

from pydantic import BaseModel

from app.ai.gemini_client import AIError, GeminiService, pick_recommended_models
from app.config import load_settings, mask_secret
from app.db.migrations import init_db
from app.db.session import Database


class CountAnswer(BaseModel):
    speakers: int | None
    answer_basis: str


GROUNDING_PROMPT = """Text: "The meeting had 3 speakers and lasted one hour."
Question: How many speakers were at the meeting? Use only the text.
Return JSON with "speakers" (a number, or null if the text does not say) and "answer_basis" (quote the words you used)."""


def main() -> int:
    settings = load_settings()
    print(f"\nGEMINI_API_KEY: {mask_secret(settings.gemini_api_key)}")
    if not settings.gemini_api_key:
        print("Open .env and paste your key after GEMINI_API_KEY= , save the file, then run this again.")
        return 1

    db = Database(settings.database_path)
    init_db(db)
    svc = GeminiService(settings, db)

    model = settings.model_fast or settings.model_smart
    if not model:
        print("No model set in .env yet, so picking one from the models your key can see…")
        try:
            model = pick_recommended_models(svc.list_models())["fast"]
        except AIError as e:
            print(f"FAILED: {e}")
            return 1
        if not model:
            print("FAILED: no suitable text model found. Run scripts\\list_gemini_models.py to see the list.")
            return 1
    print(f"Model: {model}")
    print(f"Calls used today (this app): {svc.calls_today()} / {settings.gemini_daily_call_limit}\n")

    passed = True
    t = time.perf_counter()
    try:
        res = svc.generate("connection_test", "Reply with the single word: OK", model=model, temperature=0, max_retries=1)
        print(f"{'Test 1 (plain text, ' + model + ')':40} PASS  {time.perf_counter() - t:4.1f}s  reply={res.text.strip()[:30]!r}")
    except AIError as e:
        passed = False
        print(f"{'Test 1 (plain text, ' + model + ')':40} FAIL  {e}")

    # Structured JSON on every configured model (breakdowns use the smart model with JSON output).
    json_models = [model] + ([settings.model_smart] if settings.model_smart and settings.model_smart != model else [])
    for i, m in enumerate(json_models, start=2):
        t = time.perf_counter()
        label = f"Test {i} (JSON, {m})"
        try:
            res = svc.generate("connection_test_json", GROUNDING_PROMPT, model=m, schema=CountAnswer, temperature=0, max_retries=1)
            parsed: CountAnswer = res.parsed  # type: ignore[assignment]
            good = parsed.speakers == 3
            passed = passed and good
            print(f"{label:40} {'PASS' if good else 'FAIL'}  {time.perf_counter() - t:4.1f}s  "
                  f"speakers={parsed.speakers}  basis={parsed.answer_basis[:50]!r}")
        except AIError as e:
            passed = False
            print(f"{label:40} FAIL  {e}")

    print(f"\nCalls used today (this app): {svc.calls_today()} / {settings.gemini_daily_call_limit}")
    print("\nGemini connection: " + ("PASSED" if passed else "FAILED"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
