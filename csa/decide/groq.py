"""Minimal async Groq chat client returning schema-validated JSON (LG06 LLMProvider)."""

from __future__ import annotations

import time
from typing import Any, TypeVar

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from csa.decide.completion import CompletionError

logger = structlog.get_logger(__name__)

ATTEMPTS = 2
_RETRYABLE = {429, 500, 502, 503, 504}
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
T = TypeVar("T", bound=BaseModel)


class GroqError(CompletionError):
    """Secret-free provider failure; ``code`` is safe to log and persist."""

    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        super().__init__(code)


def _json_validate_failed(response: httpx.Response) -> bool:
    """Groq's 400 for a reply that is not valid JSON. Only the fixed ``error.code`` is read:
    the body also carries the model's text (``failed_generation``), which never leaves here."""
    if response.status_code != 400:
        return False
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return False
    return isinstance(error, dict) and error.get("code") == "json_validate_failed"


class GroqClient:
    def __init__(self, api_key: str, model: str, http_client: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self.model = model
        self._http = http_client

    async def json_completion(
        self,
        schema: type[T],
        *,
        system: str,
        user: str,
        timeout: float,
        max_tokens: int = 1500,
    ) -> T:
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
            "max_completion_tokens": max_tokens,
            "reasoning_effort": "low",
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        started = time.monotonic()
        # One retry on timeout, a transient upstream failure or a malformed-JSON 400 (the model
        # wrote invalid JSON; another sample usually parses); each attempt gets half the budget
        # so the pair still fits inside the caller's node deadline.
        response: httpx.Response | None = None
        for attempt in range(ATTEMPTS):
            try:
                response = await self._http.post(
                    GROQ_URL,
                    json=body,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=timeout / ATTEMPTS,
                )
            except httpx.TimeoutException as exc:
                if attempt + 1 < ATTEMPTS:
                    continue
                raise GroqError("groq_timeout") from exc
            except httpx.HTTPError as exc:
                if attempt + 1 < ATTEMPTS:
                    continue
                raise GroqError("groq_transport_error") from exc
            retryable = response.status_code in _RETRYABLE or _json_validate_failed(response)
            if retryable and attempt + 1 < ATTEMPTS:
                continue
            break
        if response is not None and _json_validate_failed(response):
            raise GroqError("groq_invalid_output") from None
        if response is None or not response.is_success:
            status = response.status_code if response is not None else None
            raise GroqError(f"groq_http_{status}", status_code=status)
        try:
            content = response.json()["choices"][0]["message"]["content"]
            parsed = schema.model_validate_json(content)
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise GroqError("groq_invalid_output") from exc
        logger.info(
            "llm_call",
            model=self.model,
            schema=schema.__name__,
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        return parsed
