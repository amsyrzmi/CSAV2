"""Minimal async Groq chat client returning schema-validated JSON (LG06 LLMProvider)."""

from __future__ import annotations

import time
from typing import Any, TypeVar

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from csa.decide.completion import CompletionError
from csa.metering.meter import Usage, UsageMeter

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


def _count(value: object) -> int | None:
    """A non-negative integer token count, or None (a bool is not a count)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _usage(response: httpx.Response, model: str, schema_name: str) -> Usage | None:
    """The token usage a response reports, or None when it carries none.

    A response with no ``usage`` block (a timeout body, most 5xx) is not an error. A block that is
    present but unusable is logged by shape only, and never raises: reading usage must not break
    the call it measures.
    """
    try:
        body = response.json()
    except ValueError:
        return None
    block = body.get("usage") if isinstance(body, dict) else None
    if block is None:
        return None
    prompt = _count(block.get("prompt_tokens")) if isinstance(block, dict) else None
    completion = _count(block.get("completion_tokens")) if isinstance(block, dict) else None
    if prompt is None or completion is None:
        logger.warning(
            "usage_block_unusable", provider="groq", model=model, status=response.status_code
        )
        return None
    return Usage("groq", model, prompt, completion, schema_name)


class GroqClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        http_client: httpx.AsyncClient,
        url: str = GROQ_URL,
        *,
        meter: UsageMeter,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._http = http_client
        self._url = url
        self._meter = meter

    async def json_completion(
        self,
        schema: type[T],
        *,
        system: str,
        user: str,
        timeout: float,
        max_tokens: int = 1500,
    ) -> T:
        # Fail before any spend: a call with no company bound to it never reaches the provider.
        self._meter.require_context()
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
                    self._url,
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
            # Every response that reports usage was billed, including one that is retried or
            # whose reply fails validation below.
            usage = _usage(response, self.model, schema.__name__)
            if usage is not None:
                await self._meter.record(usage)
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
