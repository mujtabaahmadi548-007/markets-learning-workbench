"""Number validator: rejects invented numbers, accepts correct and rounded ones."""
from __future__ import annotations

from app.ai import validator as v

SOURCE = """Gold rose 2.47% to $3,651.85 an ounce on Thursday, its biggest gain since May.
The 10-year Treasury yield fell to 4.05%. Analysts at the bank expect $4,000 by 2027.
The S&P 500 slipped 0.3%."""


def allowed() -> list[float]:
    return v.allowed_numbers(SOURCE)


def test_extract_ignores_names_with_digits() -> None:
    nums = [raw for raw, _, _ in v.extract_numbers("The S&P 500 and the Nasdaq-100 fell; the 10-year yield rose in Q3.")]
    assert nums == []


def test_accepts_exact_and_rounded_numbers() -> None:
    a = allowed()
    assert v.invalid_numbers("Gold rose 2.47% to $3,651.85.", a) == []
    assert v.invalid_numbers("Gold rose about 2.5% to roughly 3,652.", a) == []  # rounded from the source
    assert v.invalid_numbers("The yield fell to 4.05%.", a) == []
    assert v.invalid_numbers("Analysts expect $4,000 by 2027.", a) == []


def test_rejects_invented_numbers() -> None:
    a = allowed()
    assert v.invalid_numbers("Gold rose 3.1% today.", a) == ["3.1"]
    assert v.invalid_numbers("This was the biggest gain since 2008.", a) == ["2008"]
    assert v.invalid_numbers("Some expect $5,000.", a) == ["$5,000"]


def test_find_invalid_skips_id_fields() -> None:
    data = {"summary": "Gold rose 2.47%.", "likely_drivers": [{"driver": "Fed", "evidence_article_ids": [12345]}],
            "symbol": "XAU"}
    assert v.find_invalid(data, allowed(), ("evidence_article_ids", "symbol")) == []


def test_strip_invalid_removes_only_bad_sentences_and_items() -> None:
    a = allowed()
    data = {"what_happened": "Gold rose 2.47%. It was the best day in 12 years. Buyers returned.",
            "facts": ["Yield fell to 4.05%", "Volume hit 900 tonnes"]}
    out = v.strip_invalid(data, a)
    assert out["what_happened"] == "Gold rose 2.47%. Buyers returned."
    assert out["facts"] == ["Yield fell to 4.05%"]


def test_whole_field_invalid_becomes_not_enough_information() -> None:
    assert v.strip_invalid("Up 99%.", allowed()) == "Not enough information."
