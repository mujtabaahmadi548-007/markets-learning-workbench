"""Links news articles to assets.

Pass 1 - keyword/alias rules (free, instant). A match in the headline gives high confidence (0.9),
in the description 0.7, in the article body 0.5.
Pass 2 - Gemini (fast model) for articles the rules could not place confidently, in small batches,
and only while the daily AI budget has room. Gemini may only choose from the tracked symbols.
"""
from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

# symbol -> list of regex patterns (case-insensitive unless the pattern starts with (?-i))
RULES: dict[str, list[str]] = {
    "BTC": [r"\bbitcoin\b", r"\bBTC\b", r"\bspot bitcoin etfs?\b"],
    "ETH": [r"\bethereum\b", r"\bether\b", r"\bETH\b"],
    "SOL": [r"\bsolana\b", r"\bSOL\b(?!\w)"],
    "BNB": [r"\bBNB\b", r"\bbinance coin\b", r"\bbnb chain\b"],
    "XRP": [r"\bXRP\b", r"\bripple\b"],
    "XAU": [r"\bgold\b(?!man)", r"\bbullion\b", r"\bXAU\b", r"\bprecious metals?\b"],
    "XAG": [r"\bsilver\b", r"\bXAG\b", r"\bprecious metals?\b"],
    "WTI": [r"\bWTI\b", r"\bcrude\b", r"\boil prices?\b", r"\bOPEC\+?\b", r"\bwest texas\b", r"\boil (output|supply|demand|market)\b"],
    "BRENT": [r"\bbrent\b", r"\bcrude\b", r"\boil prices?\b", r"\bOPEC\+?\b", r"\boil (output|supply|demand|market)\b"],
    "NATGAS": [r"\bnatural gas\b", r"\bnat-?gas\b", r"\bLNG\b", r"\bhenry hub\b"],
    "AAPL": [r"\bapple\b", r"\biphone\b", r"\bAAPL\b", r"\btim cook\b"],
    "MSFT": [r"\bmicrosoft\b", r"\bMSFT\b", r"\bazure\b", r"\bsatya nadella\b"],
    "NVDA": [r"\bnvidia\b", r"\bNVDA\b", r"\bjensen huang\b"],
    "AMZN": [r"\bamazon\b", r"\bAMZN\b", r"\bAWS\b", r"\bandy jassy\b"],
    "GOOGL": [r"\balphabet\b", r"\bgoogle\b", r"\bGOOGL?\b", r"\bsundar pichai\b"],
    "META": [r"(?-i:\bMeta\b)", r"(?-i:\bMETA\b)", r"\bmeta platforms\b", r"\bfacebook\b", r"\binstagram\b", r"\bzuckerberg\b"],
    "TSLA": [r"\btesla\b", r"\bTSLA\b"],
    "SPY": [r"\bS&P 500\b", r"\bS&P500\b", r"\bSPY\b", r"\bwall street\b", r"\bstock market\b", r"\bU\.?S\.? stocks\b"],
    "QQQ": [r"\bnasdaq\b", r"\bQQQ\b", r"\btech stocks\b"],
    "SPX": [r"\bS&P 500\b", r"\bS&P500\b", r"\bwall street\b", r"\bstock market\b", r"\bU\.?S\.? stocks\b"],
    "IXIC": [r"\bnasdaq\b", r"\btech stocks\b"],
    "DXY": [r"\bdollar index\b", r"\bDXY\b", r"\bgreenback\b", r"\bU\.?S\.? dollar\b", r"\bdollar (rises|falls|gains|slips|weakens|strengthens|rallies)\b"],
    "US10Y": [r"\btreasury yields?\b", r"\b10-year\b", r"\bbond yields?\b", r"\btreasuries\b"],
    "USDBROAD": [r"\bdollar index\b", r"\bgreenback\b", r"\bbroad dollar\b"],
}
# Macro topics that move yields and the dollar (spec: "Fed", "yields" -> macro).
MACRO = [r"\bfed\b", r"\bfederal reserve\b", r"\bFOMC\b", r"\bpowell\b", r"\binterest rates?\b", r"\brate (cut|hike)s?\b",
         r"\binflation\b", r"\bCPI\b", r"\bPCE\b", r"\bpayrolls?\b", r"\bjobs report\b", r"\bunemployment\b",
         r"\btariffs?\b", r"\brecession\b"]
MACRO_TARGETS = {"US10Y": 0.6, "DXY": 0.5}

_COMPILED: dict[str, list[re.Pattern]] = {}


def _compile(p: str) -> re.Pattern:
    if p.startswith("(?-i:"):
        return re.compile(p)
    return re.compile(p, re.I)


def compiled_rules() -> dict[str, list[re.Pattern]]:
    if not _COMPILED:
        for sym, pats in RULES.items():
            _COMPILED[sym] = [_compile(p) for p in pats]
        _COMPILED["__macro__"] = [_compile(p) for p in MACRO]
    return _COMPILED


def keyword_tags(title: str, description: str | None = None, body: str | None = None,
                 allowed: set[str] | None = None) -> dict[str, float]:
    """symbol -> confidence from the keyword rules."""
    rules = compiled_rules()
    out: dict[str, float] = {}
    fields = ((title or "", 0.9), (description or "", 0.7), ((body or "")[:6000], 0.5))
    for sym, pats in rules.items():
        if sym == "__macro__":
            continue
        for text, conf in fields:
            if text and any(p.search(text) for p in pats):
                out[sym] = max(out.get(sym, 0.0), conf)
                break
    for text, conf in fields:
        if text and any(p.search(text) for p in rules["__macro__"]):
            for sym, base in MACRO_TARGETS.items():
                out[sym] = max(out.get(sym, 0.0), round(base * conf / 0.9, 2))
            break
    if allowed is not None:
        out = {k: v for k, v in out.items() if k in allowed}
    return out


# ---------------------------------------------------------------- AI pass
class TagItem(BaseModel):
    id: int
    symbols: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"


class TagBatch(BaseModel):
    items: list[TagItem]


CONF = {"low": 0.4, "medium": 0.6, "high": 0.8}


def ai_tag_prompt(articles: list[dict], symbols: dict[str, str]) -> str:
    sym_list = "\n".join(f"- {s}: {n}" for s, n in symbols.items())
    arts = "\n".join(f'[{a["id"]}] {a["title"]}' + (f' — {a["description"][:300]}' if a.get("description") else "")
                     for a in articles)
    return (
        "Task: for each news item, list which of the tracked assets it is clearly about or likely to affect directly.\n"
        "Use only the headline and description given. If an item is not related to any tracked asset, return an empty list.\n"
        f"Tracked assets (use these symbols only):\n{sym_list}\n\nNews items:\n{arts}\n\n"
        'Return JSON: {"items": [{"id": <id>, "symbols": [...], "confidence": "low|medium|high"}]} with one entry per item.'
    )


def parse_ai_tags(batch: TagBatch, valid_ids: set[int], symbols: set[str]) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    for it in batch.items:
        if it.id not in valid_ids:
            continue
        syms = [s.upper() for s in it.symbols if s.upper() in symbols]
        out[it.id] = {s: CONF[it.confidence] for s in syms}
    return out
