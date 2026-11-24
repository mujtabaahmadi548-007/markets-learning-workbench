"""The single wrapper around the Gemini API (official `google-genai` SDK).

Every call goes through `GeminiService.generate()`, which:
- refuses to run without a key or when the daily call limit is reached,
- uses the shared system prompt (app.ai.prompts.SYSTEM_PROMPT),
- asks for structured JSON when a pydantic schema is given and validates the result,
- retries 5xx errors with backoff and short 429 waits; longer quota waits raise AIQuotaExceeded,
- logs every attempt (success or failure) into the ai_calls table,
- never prints or logs the API key (errors are passed through `redact`).
The number validator is added in Stage 3 (app/ai/validator.py).
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select

from app.ai.prompts import SYSTEM_PROMPT
from app.config import Settings
from app.db.session import Database
from app.logging_setup import redact
from app.models.tables import AICall
from app.timeutil import gemini_day_start, utcnow

log = logging.getLogger(__name__)

Tier = Literal["fast", "smart"]


class AIError(Exception):
    """Any Gemini failure. The message is safe to show in the UI (no secrets)."""


class AINotConfigured(AIError):
    pass


class AIDailyLimitReached(AIError):
    pass


class AIQuotaExceeded(AIError):
    """Google returned 429 (rate limit or daily free quota used up)."""


@dataclass
class AIResult:
    text: str
    model: str
    tokens_in: int | None
    tokens_out: int | None
    parsed: BaseModel | None = None


def is_daily_quota_error(text: str | None) -> bool:
    """A 429 caused by the per-DAY quota (not the per-minute rate limit), judged from Google's message."""
    t = (text or "").lower()
    return "429" in t and ("per day" in t or "perday" in t or "daily" in t)


def _retry_delay_seconds(err: Any) -> float | None:
    """Read Google's suggested wait ('retryDelay': '27s') from a 429 error, if present."""
    try:
        details = (err.details or {}).get("error", {}).get("details", []) if isinstance(err.details, dict) else []
        for d in details:
            delay = d.get("retryDelay")
            if delay:
                m = re.match(r"([\d.]+)s", str(delay))
                if m:
                    return float(m.group(1))
    except Exception:  # noqa: BLE001
        return None
    return None


class GeminiService:
    def __init__(self, settings: Settings, db: Database | None) -> None:
        self.settings = settings
        self.db = db
        self._client_obj = None

    # ------------------------------------------------------------------ status
    @property
    def configured(self) -> bool:
        return bool(self.settings.gemini_api_key)

    def resolve_model(self, tier: Tier) -> str:
        model = self.settings.model_fast if tier == "fast" else self.settings.model_smart
        if not model:
            raise AINotConfigured(
                "No Gemini model set. Run scripts\\list_gemini_models.py and set GEMINI_MODEL_FAST / GEMINI_MODEL_SMART in .env."
            )
        return model

    def calls_today(self) -> int:
        """Gemini requests sent since the start of Google's quota day (midnight Pacific)."""
        if self.db is None:
            return 0
        with self.db.session() as s:
            return int(s.scalar(select(func.count(AICall.id)).where(AICall.created_at >= gemini_day_start())) or 0)

    def remaining_today(self) -> int:
        return max(0, self.settings.gemini_daily_call_limit - self.calls_today())

    # Automatic background jobs (tagging, auto-breakdowns, briefing) may use at most this share of the
    # daily limit, so the rest stays available for things you click on.
    BACKGROUND_SHARE = 0.7

    def can_run_background(self) -> bool:
        return self.configured and bool(self.settings.model_fast or self.settings.model_smart) and \
            self.calls_today() < int(self.settings.gemini_daily_call_limit * self.BACKGROUND_SHARE)

    # ------------------------------------------------------------------ client
    def _client(self):  # noqa: ANN202 - genai.Client
        if not self.configured:
            raise AINotConfigured("GEMINI_API_KEY is empty. Open .env and paste your key after GEMINI_API_KEY=")
        if self._client_obj is None:
            from google import genai
            from google.genai import types

            # milliseconds. A busy model can hang instead of answering 503; don't wait forever.
            http = {"timeout": 60_000}
            if self.settings.gemini_api_version:
                http["api_version"] = self.settings.gemini_api_version
            if self.settings.gemini_base_url:  # tests / local fake server only
                http["base_url"] = self.settings.gemini_base_url
            self._client_obj = genai.Client(api_key=self.settings.gemini_api_key, http_options=types.HttpOptions(**http))
        return self._client_obj

    def _log_call(self, purpose: str, model: str, success: bool, tokens_in: int | None = None,
                  tokens_out: int | None = None, error: str | None = None) -> None:
        if self.db is None:
            return
        try:
            with self.db.session() as s:
                s.add(AICall(purpose=purpose, model=model, tokens_in=tokens_in, tokens_out=tokens_out,
                             success=success, error=redact(error)[:2000] if error else None, created_at=utcnow()))
        except Exception:  # noqa: BLE001 - logging the call must never break the caller
            log.exception("could not record ai_call")

    # ------------------------------------------------------------------ calls
    def list_models(self) -> list[dict[str, Any]]:
        """Models this key can see, with the actions each supports."""
        out = []
        for m in self._client().models.list():
            out.append({
                "name": (m.name or "").removeprefix("models/"),
                "display_name": m.display_name or "",
                "description": m.description or "",
                "input_token_limit": m.input_token_limit,
                "output_token_limit": m.output_token_limit,
                "supported_actions": list(m.supported_actions or []),
            })
        return out

    def _switch_to_fast(self, tier: Tier, explicit_model: str | None, current: str) -> bool:
        """When the smart model is overloaded or out of quota, finish the job with the fast model instead of
        waiting or failing. The model actually used is stored with every result, so the switch is visible."""
        fast = self.settings.model_fast
        return tier == "smart" and explicit_model is None and bool(fast) and current != fast

    def day_quota_used_up(self, model: str) -> bool:
        """True when Google already refused `model` today because its free PER-DAY quota is used up.

        Free-tier quotas are per model (gemini-3.6-flash allowed 20 requests a day in Nov 2026), so the fast
        model usually still works. Read from the call log, because every website build is a new process."""
        if self.db is None:
            return False
        with self.db.session() as s:
            errs = s.scalars(select(AICall.error).where(
                AICall.created_at >= gemini_day_start(), AICall.model == model, AICall.success.is_(False),
                AICall.error.like("HTTP 429%"))).all()
        return any(is_daily_quota_error(e) for e in errs)

    def generate(
        self,
        purpose: str,
        prompt: str,
        *,
        tier: Tier = "smart",
        model: str | None = None,
        schema: type[BaseModel] | None = None,
        system: str = SYSTEM_PROMPT,
        temperature: float = 0.2,
        max_retries: int = 2,
    ) -> AIResult:
        from google.genai import errors, types

        client = self._client()
        model_name = model or self.resolve_model(tier)
        if self._switch_to_fast(tier, model, model_name) and self.day_quota_used_up(model_name):
            log.info("Gemini %s is out of its daily free quota; using the fast model %s for %s", model_name,
                     self.settings.model_fast, purpose)
            model_name = self.settings.model_fast

        cfg: dict[str, Any] = {"system_instruction": system, "temperature": temperature}
        if schema is not None:
            cfg["response_mime_type"] = "application/json"
            cfg["response_json_schema"] = schema.model_json_schema()
        config = types.GenerateContentConfig(**cfg)

        attempt = 0
        while True:
            if self.calls_today() >= self.settings.gemini_daily_call_limit:
                raise AIDailyLimitReached(
                    f"Daily AI limit reached ({self.settings.gemini_daily_call_limit} calls). It resets at midnight Pacific time."
                )
            attempt += 1
            try:
                resp = client.models.generate_content(model=model_name, contents=prompt, config=config)
            except errors.ClientError as e:  # 4xx
                msg = redact(f"HTTP {e.code}: {e.message or e.status or ''}")
                self._log_call(purpose, model_name, False, error=msg)
                if e.code == 429:
                    wait = _retry_delay_seconds(e)
                    if attempt <= max_retries and wait is not None and wait <= 30:
                        log.warning("Gemini 429, waiting %.0fs then retrying (%s)", wait, purpose)
                        time.sleep(wait + 1)
                        continue
                    if self._switch_to_fast(tier, model, model_name):
                        log.warning("Gemini %s quota reached (%s); using the fast model %s for %s", model_name, msg,
                                    self.settings.model_fast, purpose)
                        model_name = self.settings.model_fast
                        continue
                    raise AIQuotaExceeded(f"Gemini rate limit or daily quota reached. {msg}") from None
                raise AIError(f"Gemini rejected the request. {msg}") from None
            except errors.ServerError as e:  # 5xx (often "model overloaded" / "deadline expired")
                msg = redact(f"HTTP {e.code}: {e.message or e.status or ''}")
                self._log_call(purpose, model_name, False, error=msg)
                if self._switch_to_fast(tier, model, model_name):
                    log.warning("Gemini %s busy (%s); using the fast model %s for %s", model_name, msg, self.settings.model_fast, purpose)
                    model_name = self.settings.model_fast
                    continue
                if attempt <= max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise AIError(f"Gemini server error. {msg}") from None
            except errors.APIError as e:
                msg = redact(f"HTTP {getattr(e, 'code', '?')}: {getattr(e, 'message', '')}")
                self._log_call(purpose, model_name, False, error=msg)
                raise AIError(f"Gemini error. {msg}") from None
            except Exception as e:  # noqa: BLE001 - network errors, timeouts
                msg = redact(f"{type(e).__name__}: {e}")
                self._log_call(purpose, model_name, False, error=msg)
                if self._switch_to_fast(tier, model, model_name):
                    log.warning("Gemini %s did not answer (%s); using the fast model %s for %s", model_name, msg,
                                self.settings.model_fast, purpose)
                    model_name = self.settings.model_fast
                    continue
                if attempt <= max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise AIError(f"Could not reach Gemini. {msg}") from None

            usage = resp.usage_metadata
            tokens_in = usage.prompt_token_count if usage else None
            tokens_out = usage.candidates_token_count if usage else None
            text = resp.text or ""
            parsed = None
            if schema is not None:
                try:
                    parsed = schema.model_validate_json(text)
                except ValidationError as e:
                    self._log_call(purpose, model_name, False, tokens_in, tokens_out, error=f"invalid JSON: {e.error_count()} errors")
                    raise AIError("Gemini returned JSON that does not match the expected format.") from None
            self._log_call(purpose, model_name, True, tokens_in, tokens_out)
            return AIResult(text=text, model=model_name, tokens_in=tokens_in, tokens_out=tokens_out, parsed=parsed)


def pick_recommended_models(models: list[dict[str, Any]]) -> dict[str, str | None]:
    """Choose a fast and a smart text model from the list the key can use.

    Only stable text models that support generateContent are considered (no preview, TTS, image,
    audio, live, embedding, transcribe models). Higher version numbers win.
    """
    excluded = ("preview", "exp", "tts", "image", "audio", "live", "embed", "transcribe", "robotics",
                "computer-use", "veo", "imagen", "aqa", "learnlm", "gemma", "nano", "omni", "genie", "translate", "latest")

    def version_key(name: str) -> tuple[float, ...]:
        nums = re.findall(r"\d+(?:\.\d+)?", name)
        return tuple(float(n) for n in nums[:2]) or (0.0,)

    text_models = [
        m["name"] for m in models
        if "generateContent" in m.get("supported_actions", [])
        and m["name"].startswith("gemini")
        and not any(x in m["name"] for x in excluded)
        # Gemini 2.x models are being retired/rerouted (Google: 2.5 Flash/Pro reroute on 1 Dec 2026).
        and version_key(m["name"])[0] >= 3
    ]
    lite = sorted([n for n in text_models if "flash-lite" in n], key=version_key, reverse=True)
    flash = sorted([n for n in text_models if "flash" in n and "lite" not in n], key=version_key, reverse=True)
    pro = sorted([n for n in text_models if "pro" in n], key=version_key, reverse=True)
    fast_candidates = lite + flash
    smart_candidates = flash + pro + lite
    return {
        "fast": fast_candidates[0] if fast_candidates else None,
        "smart": smart_candidates[0] if smart_candidates else None,
        "pro": pro[0] if pro else None,
        "fast_candidates": fast_candidates,
        "smart_candidates": smart_candidates,
    }


def json_dumps_safe(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
