"""Minimal async Gemini JSON-completion client used for composer failover."""

from __future__ import annotations

import time
from typing import Any, TypeVar
from urllib.parse import quote

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from csa.decide.completion import CompletionError
from csa.metering.meter import Usage, UsageMeter

logger = structlog.get_logger(__name__)

ATTEMPTS = 2
_MIN_ATTEMPT_S = 0.5
_RETRYABLE = {429, 500, 502, 503, 504}
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
T = TypeVar("T", bound=BaseModel)
_UNSUPPORTED_SCHEMA = "gemini_unsupported_schema"


class GeminiClient:
    """Call the Gemini generateContent API with validated JSON output."""

    def __init__(
        self, api_key: str, model: str, http_client: httpx.AsyncClient, *, meter: UsageMeter
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._http = http_client
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
        generation_config: dict[str, Any] = {
            "temperature": 0,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        }
        try:
            generation_config["responseSchema"] = _response_schema(schema.model_json_schema())
        except CompletionError as exc:
            if exc.code != _UNSUPPORTED_SCHEMA:
                raise
            # Gemini structured output cannot express this schema ($ref, nested models).
            # Send plain JSON mode instead; the prompt names the keys and the reply is
            # still validated against the full schema below.
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": generation_config,
        }
        started = time.monotonic()
        deadline = started + timeout
        response: httpx.Response | None = None
        for attempt in range(ATTEMPTS):
            try:
                response = await self._http.post(
                    GEMINI_URL.format(model=quote(self.model, safe="")),
                    json=body,
                    headers={"x-goog-api-key": self._api_key},
                    timeout=max(deadline - time.monotonic(), _MIN_ATTEMPT_S),
                )
            except httpx.TimeoutException as exc:
                raise CompletionError("gemini_timeout") from exc
            except httpx.HTTPError as exc:
                if attempt + 1 < ATTEMPTS and deadline - time.monotonic() > _MIN_ATTEMPT_S:
                    continue
                raise CompletionError("gemini_transport_error") from exc
            # Every response that reports usage was billed, including one that is retried or
            # whose reply fails validation below.
            usage = _usage(response, self.model, schema.__name__)
            if usage is not None:
                await self._meter.record(usage)
            if (
                response.status_code in _RETRYABLE
                and attempt + 1 < ATTEMPTS
                and deadline - time.monotonic() > _MIN_ATTEMPT_S
            ):
                continue
            break
        if response is None or not response.is_success:
            status = response.status_code if response is not None else None
            raise CompletionError(f"gemini_http_{status}", status_code=status)
        try:
            content = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            parsed = schema.model_validate_json(content)
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise CompletionError("gemini_invalid_output") from exc
        logger.info(
            "llm_call",
            provider="gemini",
            model=self.model,
            schema=schema.__name__,
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        return parsed


def _count(value: object) -> int | None:
    """A non-negative integer token count, or None (a bool is not a count)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _usage(response: httpx.Response, model: str, schema_name: str) -> Usage | None:
    """The token usage a response reports, or None when it carries none.

    Output tokens are the candidates plus the thinking tokens, both billed as output. Gemini
    leaves a count out when it is zero (thinking can use the whole output budget), so only the
    prompt count is required. A block that is present but unusable is logged by shape only, and
    never raises: reading usage must not break the call it measures.
    """
    try:
        body = response.json()
    except ValueError:
        return None
    block = body.get("usageMetadata") if isinstance(body, dict) else None
    if block is None:
        return None
    prompt = candidates = thoughts = None
    if isinstance(block, dict):
        prompt = _count(block.get("promptTokenCount"))
        candidates = _count(block.get("candidatesTokenCount", 0))
        thoughts = _count(block.get("thoughtsTokenCount", 0))
    if prompt is None or candidates is None or thoughts is None:
        logger.warning(
            "usage_block_unusable", provider="gemini", model=model, status=response.status_code
        )
        return None
    return Usage("gemini", model, prompt, candidates + thoughts, schema_name)


def _response_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Keep Pydantic's schema to the subset supported by Gemini structured output."""
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise CompletionError(_UNSUPPORTED_SCHEMA)
    converted_properties: dict[str, Any] = {}
    for name, definition in properties.items():
        if not isinstance(definition, dict):
            raise CompletionError(_UNSUPPORTED_SCHEMA)
        converted_properties[name] = _property_schema(definition)
    output: dict[str, Any] = {
        "type": "OBJECT",
        "properties": converted_properties,
    }
    required = schema.get("required", [])
    if isinstance(required, list) and required:
        output["required"] = required
    return output


def _property_schema(schema: dict[str, Any]) -> dict[str, Any]:
    kind = schema.get("type")
    if kind == "array":
        item = schema.get("items")
        if not isinstance(item, dict):
            raise CompletionError(_UNSUPPORTED_SCHEMA)
        return {"type": "ARRAY", "items": _property_schema(item)}
    if kind not in {"string", "number", "integer", "boolean", "object"}:
        raise CompletionError(_UNSUPPORTED_SCHEMA)
    output: dict[str, Any] = {"type": kind.upper()}
    enum = schema.get("enum")
    if isinstance(enum, list):
        output["enum"] = enum
    return output
