"""Learning pages and API: learning paths, lessons, glossary, quizzes."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.api.deps import state
from app.api.pages import _ctx
from app.web import templates

router = APIRouter()


# ---------------------------------------------------------------- lessons
@router.get("/learn", response_class=HTMLResponse)
def learn_page(request: Request) -> HTMLResponse:
    st = state(request)
    return templates.TemplateResponse(request, "learn.html", _ctx(request, "/learn", paths=st.learning.paths(),
                                                                   stats=st.learning.stats()))


@router.get("/learn/{lesson_id}", response_class=HTMLResponse)
def lesson_page(request: Request, lesson_id: int) -> HTMLResponse:
    st = state(request)
    lesson = st.learning.lesson(lesson_id)
    if lesson is None:
        raise HTTPException(404, "Lesson not found")
    st.learning.mark_seen([c["slug"] for c in lesson["concepts"]])
    examples = []
    for sym in lesson["symbols"]:
        examples += st.news.query(symbol=sym, hours=72, sort="important", limit=3)
    seen, uniq = set(), []
    for e in sorted(examples, key=lambda x: -x["rank"]):
        if e["id"] not in seen:
            seen.add(e["id"])
            uniq.append(e)
    quotes = [v for v in st.feeds.views() if v["symbol"] in lesson["symbols"]]
    return templates.TemplateResponse(request, "lesson.html", _ctx(request, "/learn", lesson=lesson, examples=uniq[:6],
                                                                    quotes=quotes))


@router.post("/api/lessons/{lesson_id}/complete")
def lesson_complete(request: Request, lesson_id: int, done: bool = True) -> dict[str, Any]:
    state(request).learning.complete_lesson(lesson_id, done)
    return {"ok": True}


# ---------------------------------------------------------------- glossary
@router.get("/glossary", response_class=HTMLResponse)
def glossary_page(request: Request) -> HTMLResponse:
    st = state(request)
    concepts = st.learning.concepts()
    cats = sorted({c["category"] for c in concepts})
    return templates.TemplateResponse(request, "glossary.html", _ctx(request, "/glossary", concepts=concepts, categories=cats))


@router.get("/glossary/{slug}", response_class=HTMLResponse)
def concept_page(request: Request, slug: str) -> HTMLResponse:
    st = state(request)
    c = st.learning.concept(slug)
    if c is None:
        raise HTTPException(404, "Concept not found")
    st.learning.mark_seen([slug])
    arts = st.learning.articles_for_concept(slug, c["name"])
    return templates.TemplateResponse(request, "concept.html", _ctx(request, "/glossary", c=c, articles=arts,
                                                                     ai_ready=st.gemini.configured))


@router.post("/api/concepts/{slug}/explain")
async def concept_explain(request: Request, slug: str) -> dict[str, Any]:
    st = state(request)
    if not st.gemini.configured:
        raise HTTPException(503, "Gemini is not configured (GEMINI_API_KEY in .env).")
    return {"concept": await asyncio.to_thread(st.learning.explain_concept, slug)}


class ConceptEdit(BaseModel):
    short: str
    long: str
    example: str = ""


@router.post("/api/concepts/{slug}")
def concept_edit(request: Request, slug: str, body: ConceptEdit) -> dict[str, Any]:
    try:
        state(request).learning.edit_concept(slug, body.short, body.long, body.example)
    except KeyError:
        raise HTTPException(404, "Concept not found") from None
    return {"ok": True}


# ---------------------------------------------------------------- quiz
@router.get("/quiz", response_class=HTMLResponse)
def quiz_page(request: Request) -> HTMLResponse:
    st = state(request)
    top = st.news.query(hours=36, sort="important", limit=5, distinct_stories=True)
    return templates.TemplateResponse(request, "quiz.html", _ctx(request, "/quiz", stats=st.learning.stats(), top=top,
                                                                  ai_ready=st.gemini.configured))


@router.get("/api/quiz/next")
def quiz_next(request: Request, mode: str = "today", limit: int = 10) -> dict[str, Any]:
    return {"questions": state(request).learning.next_questions(mode, limit)}


class Answer(BaseModel):
    question_id: int
    chosen: int


@router.post("/api/quiz/answer")
def quiz_answer(request: Request, body: Answer) -> dict[str, Any]:
    try:
        return state(request).learning.answer(body.question_id, body.chosen)
    except KeyError:
        raise HTTPException(404, "Question not found") from None


class GenRequest(BaseModel):
    source_type: str
    source_id: str
    n: int = 3


@router.post("/api/quiz/generate")
async def quiz_generate(request: Request, body: GenRequest) -> dict[str, Any]:
    st = state(request)
    if not st.gemini.configured:
        raise HTTPException(503, "Gemini is not configured (GEMINI_API_KEY in .env).")
    if body.source_type not in ("article", "concept", "lesson"):
        raise HTTPException(400, "source_type must be article, concept or lesson")
    if body.source_type == "article":
        await st.breakdowns.ensure_text(int(body.source_id))
    return await asyncio.to_thread(st.learning.generate_quiz, body.source_type, body.source_id, max(1, min(body.n, 5)))


@router.get("/api/quiz/stats")
def quiz_stats(request: Request) -> dict[str, Any]:
    return state(request).learning.stats()
