"""Shared request helpers."""
from __future__ import annotations

from fastapi import Request

from app.web import AppState


def state(request: Request) -> AppState:
    return request.app.state.mlw
