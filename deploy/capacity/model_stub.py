"""Fake model server for the capacity gate (spec section 6). Standard library only.

Answers the five model calls a customer turn makes (classify, plan, next step, compose, review)
with a valid greeting turn, after a delay close to the real model's. It tells the calls apart by
the first sentence of the system prompt. It never calls out and keeps nothing but counters.

Run it on the ``provider-egress`` network under the name ``model-stub`` (README.md).
"""

from __future__ import annotations

import json
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# (opening words of the system prompt, kind). Kept in step with csa/decide/llm.py.
KINDS = (
    ("You classify one customer message", "classify"),
    ("You choose which business agents", "plan"),
    ("You coordinate business agents", "step"),
    ("You write the customer-facing reply", "compose"),
    ("You check a customer-support reply", "review"),
)
# Seconds, before jitter: about 4.4 s for the five calls, the spec's "about 5 s a turn".
DELAY = {"classify": 0.6, "plan": 1.0, "step": 0.6, "compose": 1.6, "review": 0.6}
JITTER = 0.25

ANSWERS: dict[str, dict[str, Any]] = {
    "classify": {
        "intent": "chitchat",
        "language": "en",
        "sentiment": "neutral",
        "wants_human": False,
        "injection": False,
        "urgent": False,
        "needs": {},
    },
    "plan": {"tasks": [], "reply_strategy": "auto_reply", "priority": "normal"},
    "step": {"done": True, "tasks": []},
    "compose": {
        "reply": "Hello! Thanks for your message. How can I help you today?",
        "used_fact_ids": [],
    },
    "review": {"ok": True, "issues": []},
}

COUNTS: dict[str, int] = {}
LOCK = threading.Lock()


def kind_of(system: str) -> str | None:
    for opening, kind in KINDS:
        if system.startswith(opening):
            return kind
    return None


def completion(content: dict[str, Any]) -> dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", "content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        with LOCK:
            self._send(200, dict(COUNTS))

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            messages = json.loads(raw)["messages"]
            system = next(m["content"] for m in messages if m["role"] == "system")
        except (ValueError, KeyError, StopIteration, TypeError):
            self._send(400, {"error": {"message": "unreadable request"}})
            return
        kind = kind_of(system)
        with LOCK:
            COUNTS[kind or "unknown"] = COUNTS.get(kind or "unknown", 0) + 1
        if kind is None:
            self._send(400, {"error": {"message": "unknown call"}})
            return
        time.sleep(DELAY[kind] * random.uniform(1 - JITTER, 1 + JITTER))
        self._send(200, completion(ANSWERS[kind]))


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
