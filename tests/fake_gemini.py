"""A tiny local stand-in for the Gemini REST API, used ONLY by tests.

It lets the tests exercise the real google-genai SDK code path (request building, headers,
response parsing, error handling) without a real key and without sending anything to Google.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakeGemini:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.mode = "ok"  # ok | 429 | 500 | bad_json
        self.json_reply: dict[str, Any] = {"speakers": 3, "answer_basis": "3 speakers"}
        self.replies: list[Any] = []  # queued JSON replies (dict or callable(prompt) -> dict), used in order
        self.prompts: list[str] = []
        self.fail_models: set[str] = set()  # models that answer HTTP 500 (to test the overload fallback)
        self.day_quota_models: set[str] = set()  # models whose free per-day quota is used up (HTTP 429)
        self.text_reply = "OK"
        handler = self._make_handler()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> "FakeGemini":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _make_handler(self):  # noqa: ANN202
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # silence
                pass

            def _send(self, code: int, body: dict[str, Any]) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _record(self, body: Any) -> None:
                fake.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body})

            def do_GET(self) -> None:  # noqa: N802
                self._record(None)
                if re.search(r"/v1(beta)?/models", self.path):
                    models = [
                        {"name": "models/gemini-3.8-flash", "displayName": "Gemini 3.8 Flash", "inputTokenLimit": 1048576,
                         "supportedGenerationMethods": ["generateContent", "countTokens"]},
                        {"name": "models/gemini-3.5-flash-lite", "displayName": "Gemini 3.5 Flash-Lite", "inputTokenLimit": 1048576,
                         "supportedGenerationMethods": ["generateContent"]},
                        {"name": "models/gemini-3-flash-preview", "displayName": "Preview", "supportedGenerationMethods": ["generateContent"]},
                        {"name": "models/gemini-3.8-flash-tts", "displayName": "TTS", "supportedGenerationMethods": ["generateContent"]},
                        {"name": "models/text-embedding-005", "displayName": "Embedding", "supportedGenerationMethods": ["embedContent"]},
                    ]
                    self._send(200, {"models": models})
                else:
                    self._send(404, {"error": {"code": 404, "message": "not found", "status": "NOT_FOUND"}})

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                self._record(body)
                if fake.mode == "429":
                    self._send(429, {"error": {"code": 429, "message": "Resource has been exhausted", "status": "RESOURCE_EXHAUSTED",
                                               "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3600s"}]}})
                    return
                if any(m in self.path for m in fake.day_quota_models):
                    # wording of Google's real free-tier answer (Nov 2026)
                    self._send(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message":
                        "You exceeded your current quota. * Quota exceeded for metric: generativelanguage.googleapis.com/"
                        "generate_content_free_tier_requests, limit: 20, model: gemini-3.8-flash, description: Generate "
                        "content requests per day limit exceeded on Free Tier. Please retry in 7h56m5s.",
                        "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "28565s"}]}})
                    return
                if fake.mode == "500" or any(m in self.path for m in fake.fail_models):
                    self._send(500, {"error": {"code": 500, "message": "internal", "status": "INTERNAL"}})
                    return
                gen_cfg = body.get("generationConfig") or {}
                wants_json = gen_cfg.get("responseMimeType") == "application/json"
                prompt = "".join(p.get("text", "") for c in body.get("contents", []) for p in c.get("parts", []))
                fake.prompts.append(prompt)
                if fake.mode == "bad_json":
                    text = '{"unexpected": true}'
                elif wants_json and fake.replies:
                    nxt = fake.replies.pop(0)
                    text = json.dumps(nxt(prompt) if callable(nxt) else nxt)
                elif wants_json:
                    text = json.dumps(fake.json_reply)
                else:
                    text = fake.text_reply
                self._send(200, {
                    "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 3, "totalTokenCount": 15},
                })

        return Handler
