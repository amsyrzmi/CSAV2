"""Minimal async Gemini JSON-completion client used for composer failover."""

from __future__ import annotations

import time
from typing import Any, TypeVar
from urllib.parse import quote

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from csa.decide.completion import CompletionError

logger = structlog.get_logger(__name__)

ATTEMPTS = 2
_MIN_ATTEMPT_S = 0.5
_RETRYABLE = {429, 500, 502, 503, 504}
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
T = TypeVar("T", bound=BaseModel)


class GeminiClient:
    """Call the Gemini generateContent API with validated JSON output."""

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
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
                "responseSchema": _response_schema(schema.model_json_schema()),
            },
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


def _response_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Keep Pydantic's schema to the subset supported by Gemini structured output."""
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise CompletionError("gemini_unsupported_schema")
    converted_properties: dict[str, Any] = {}
    for name, definition in properties.items():
        if not isinstance(definition, dict):
            raise CompletionError("gemini_unsupported_schema")
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
            raise CompletionError("gemini_unsupported_schema")
        return {"type": "ARRAY", "items": _property_schema(item)}
    if kind not in {"string", "number", "integer", "boolean", "object"}:
        raise CompletionError("gemini_unsupported_schema")
    output: dict[str, Any] = {"type": kind.upper()}
    enum = schema.get("enum")
    if isinstance(enum, list):
        output["enum"] = enum
    return output
