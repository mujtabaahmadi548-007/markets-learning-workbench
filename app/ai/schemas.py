"""Structured output formats for every Gemini feature (validated with pydantic)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- A) article breakdown
class AffectedAsset(BaseModel):
    symbol: str
    direction_suggested_by_article: Literal["up", "down", "unclear"]
    mechanism: str = Field(description="cause -> effect chain, e.g. higher yields -> stronger dollar -> pressure on gold")


class ConceptRef(BaseModel):
    slug: str
    name: str
    one_line: str


class FactsVsOpinion(BaseModel):
    facts: list[str] = Field(default_factory=list)
    opinions_or_speculation: list[str] = Field(default_factory=list)


class ArticleBreakdown(BaseModel):
    what_happened: str
    why_it_matters: str
    affected_assets: list[AffectedAsset] = Field(default_factory=list)
    concepts: list[ConceptRef] = Field(default_factory=list)
    facts_vs_opinion: FactsVsOpinion = Field(default_factory=FactsVsOpinion)
    beginner_explanation: str
    basis: Literal["full_article", "headline_and_description_only"]


# ---------------------------------------------------------------- B) why did it move today?
class Driver(BaseModel):
    driver: str
    evidence_article_ids: list[int] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"]


class MoveExplanationOut(BaseModel):
    summary: str
    likely_drivers: list[Driver] = Field(default_factory=list)
    unexplained: bool
    note: str = ""


# ---------------------------------------------------------------- C) concept explanation
class ConceptExplanation(BaseModel):
    name: str
    category: Literal["macro", "rates", "currency", "commodities", "energy", "metals", "crypto", "stocks", "trading", "general"]
    short_definition: str
    long_explanation: str
    example: str = Field(description="a real-world example taken from the provided news items, or 'Not enough information.'")
    related_concepts: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- D) quiz questions + checker
class QuizQuestionOut(BaseModel):
    question: str
    options: list[str] = Field(min_length=4, max_length=4)
    correct_index: int = Field(ge=0, le=3)
    explanation: str
    option_explanations: list[str] = Field(min_length=4, max_length=4)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    concept_slugs: list[str] = Field(default_factory=list)


class QuizSet(BaseModel):
    questions: list[QuizQuestionOut]


class CheckVerdict(BaseModel):
    index: int
    answer_supported_by_source: bool
    correct_index_according_to_source: int = Field(ge=-1, le=3)
    reason: str = ""


class CheckResult(BaseModel):
    verdicts: list[CheckVerdict]


# ---------------------------------------------------------------- E) daily briefing
class BriefingWatch(BaseModel):
    what_to_watch: list[str] = Field(description="exactly 3 short sentences based ONLY on the provided stories and calendar")
