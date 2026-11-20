"""Spaced repetition (SM-2 style).

- Correct answer: interval 1 day, then 6 days, then previous interval x ease.
- Wrong answer: repetitions reset and the question comes back after 10 minutes (so missed questions
  return in the Review mode soon), and the ease drops (minimum 1.3).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

MIN_EASE = 1.3
RELEARN_DELAY = timedelta(minutes=10)


@dataclass
class SrsState:
    repetitions: int = 0
    interval_days: float = 0.0
    ease: float = 2.5


def review(state: SrsState, correct: bool, now: datetime) -> tuple[SrsState, datetime]:
    q = 4 if correct else 1  # SM-2 quality: 4 = correct with some effort, 1 = wrong
    ease = max(MIN_EASE, state.ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)))
    if not correct:
        return SrsState(repetitions=0, interval_days=0.0, ease=ease), now + RELEARN_DELAY
    reps = state.repetitions + 1
    if reps == 1:
        interval = 1.0
    elif reps == 2:
        interval = 6.0
    else:
        interval = round(max(1.0, state.interval_days) * ease, 1)
    return SrsState(repetitions=reps, interval_days=interval, ease=ease), now + timedelta(days=interval)
