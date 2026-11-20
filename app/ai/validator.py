"""Number validator: every number in an AI output must appear in the data that was given to the AI.

How it works:
1. Collect every number in the prompt (article text, prices, % moves, dates...) = the allowed numbers.
2. Find every number in the AI's text fields. A number passes if it equals an allowed number, or is an
   allowed number rounded (e.g. 3.8553 -> "3.86" or "3.9"), within a small rounding tolerance.
3. Names that contain digits ("S&P 500", "Nasdaq-100", "10-year Treasury", "Q3", "G7", "COVID-19") are ignored.
If anything fails, `generate_validated` retries once with a stricter prompt; if it still fails, the
sentences / list items with invented numbers are removed and the result is flagged validation_passed=False.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel

from app.ai.gemini_client import GeminiService

log = logging.getLogger(__name__)

EXEMPT = re.compile(
    r"S&P\s?500|S&P\s?400|S&P\s?600|Nasdaq[- ]?100|Russell\s?\d{4}|Dow\s?Jones\s?\d+|FTSE\s?100|Nikkei\s?225|DAX\s?40|"
    r"\b\d{1,2}-(?:year|month|week)(?=[- ](?:treasury|yield|note|bond|bill|maturity))|\b(?:2|5|10|30)-year\b|"
    r"\bQ[1-4]\b|\bH[12]\b|\bG-?\d{1,2}\b|COVID-19|\bWeb3\b|\b24/7\b|\bL[12]\b|\b[1-9]D\b|\bBRC-20\b|\bERC-20\b|"
    r"\bAI\d?\b|\bGPT-?\d\b|\bOPEC\+",
    re.I,
)
NUM = re.compile(r"(?<![\w.])[-−+]?\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")


def _value(raw: str) -> tuple[float, int]:
    s = raw.replace("−", "-").replace("$", "").replace(",", "").lstrip("+")
    decimals = len(s.split(".")[1]) if "." in s else 0
    return abs(float(s)), decimals


def extract_numbers(text: str) -> list[tuple[str, float, int]]:
    """(raw, absolute value, decimals) for each number in the text, ignoring names that contain digits."""
    cleaned = EXEMPT.sub(" ", text or "")
    out = []
    for m in NUM.finditer(cleaned):
        raw = m.group(0)
        try:
            v, d = _value(raw)
        except ValueError:
            continue
        out.append((raw, v, d))
    return out


def allowed_numbers(*texts: str) -> list[float]:
    vals = set()
    for t in texts:
        for _, v, _ in extract_numbers(t):
            vals.add(v)
    return sorted(vals)


def number_ok(value: float, decimals: int, allowed: list[float]) -> bool:
    for a in allowed:
        if a == value:
            return True
        if round(a, decimals) == value:  # the AI rounded an allowed number
            return True
        if value >= 10 and a > 0 and abs(a - value) / a <= 0.005:
            return True
        # Same number written in thousands/millions/billions units elsewhere (e.g. 1,500 million vs 1.5 billion)
        for k in (1e3, 1e6, 1e9):
            if abs(a * k - value) < 1e-9 or abs(a / k - value) < 1e-9:
                return True
    return False


def invalid_numbers(text: str, allowed: list[float]) -> list[str]:
    return [raw for raw, v, d in extract_numbers(text) if not number_ok(v, d, allowed)]


def _walk_strings(obj: Any, path: str = "") -> list[tuple[str, str]]:
    out = []
    if isinstance(obj, str):
        out.append((path, obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out += _walk_strings(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += _walk_strings(v, f"{path}[{i}]")
    return out


def find_invalid(data: dict[str, Any], allowed: list[float], skip_keys: tuple[str, ...] = ()) -> list[str]:
    bad = []
    for path, text in _walk_strings(data):
        if any(path.endswith(k) or f".{k}" in path or path.startswith(k) for k in skip_keys):
            continue
        bad += invalid_numbers(text, allowed)
    return sorted(set(bad))


SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def strip_invalid(obj: Any, allowed: list[float]) -> Any:
    """Remove sentences (in strings) and items (in lists of strings) that contain invented numbers."""
    if isinstance(obj, str):
        if not invalid_numbers(obj, allowed):
            return obj
        kept = [s for s in SENT_SPLIT.split(obj) if not invalid_numbers(s, allowed)]
        return " ".join(kept).strip() or "Not enough information."
    if isinstance(obj, list):
        out = []
        for v in obj:
            if isinstance(v, str) and invalid_numbers(v, allowed):
                continue
            out.append(strip_invalid(v, allowed))
        return out
    if isinstance(obj, dict):
        return {k: strip_invalid(v, allowed) for k, v in obj.items()}
    return obj


def generate_validated(gemini: GeminiService, purpose: str, prompt: str, schema: type[BaseModel], *, tier: str = "smart",
                       skip_keys: tuple[str, ...] = ("symbol", "slug", "evidence_article_ids", "basis", "confidence",
                                                     "direction_suggested_by_article", "category"),
                       temperature: float = 0.2) -> tuple[BaseModel, bool, str, str]:
    """Generate structured output and validate its numbers. Returns (result, passed, notes, model)."""
    allowed = allowed_numbers(prompt)
    res = gemini.generate(purpose, prompt, tier=tier, schema=schema, temperature=temperature)  # type: ignore[arg-type]
    data = res.parsed.model_dump()  # type: ignore[union-attr]
    bad = find_invalid(data, allowed, skip_keys)
    if not bad:
        return res.parsed, True, "", res.model  # type: ignore[return-value]
    log.info("%s: numbers not found in the data %s; retrying with a stricter prompt", purpose, bad)
    stricter = (prompt + "\n\nIMPORTANT: your previous answer contained numbers that do NOT appear in the data above: "
                + ", ".join(bad) + ". Write the answer again WITHOUT any number that is not in the data. "
                "If unsure, leave numbers out.")
    res2 = gemini.generate(purpose + "_retry", stricter, tier=tier, schema=schema, temperature=0.0)  # type: ignore[arg-type]
    data2 = res2.parsed.model_dump()  # type: ignore[union-attr]
    bad2 = find_invalid(data2, allowed, skip_keys)
    if not bad2:
        return res2.parsed, True, f"Passed after one retry (first answer had: {', '.join(bad)})", res2.model  # type: ignore[return-value]
    cleaned = strip_invalid(data2, allowed)
    # put back fields that must not be stripped
    for k in skip_keys:
        if k in data2:
            cleaned[k] = data2[k]
    log.warning("VALIDATION FAILED (%s): removed sentences containing %s", purpose, bad2)
    return schema.model_validate(cleaned), False, f"Validation failed: removed text containing {', '.join(bad2)}", res2.model
