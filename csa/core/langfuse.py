"""Langfuse observability client and event sink (LG01 §8 / LG09).

Exports redacted traces, spans, and generations to Langfuse Cloud / self-hosted
via the Langfuse batch ingestion API (``/api/public/ingestion``).
Customer text is never sent unredacted.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import structlog
from pydantic import SecretStr

logger = structlog.get_logger(__name__)


class LangfuseSink:
    """Async client that sends redacted turn and node events to Langfuse."""

    def __init__(
        self,
        public_key: str,
        secret_key: SecretStr | str,
        base_url: str = "https://cloud.langfuse.com",
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._public_key = public_key
        self._secret_key = (
            secret_key.get_secret_value() if isinstance(secret_key, SecretStr) else secret_key
        )
        self._base_url = base_url.rstrip("/")
        self._http = http_client or httpx.AsyncClient(timeout=10.0)
        self._owns_http = http_client is None

        # Basic Auth header: base64(public_key:secret_key)
        creds = f"{self._public_key}:{self._secret_key}".encode()
        self._auth_header = f"Basic {base64.b64encode(creds).decode('ascii')}"

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def send_event(
        self,
        *,
        client_id: UUID,
        trace_id: UUID,
        conversation_id: UUID | None,
        node: str,
        agent: str | None,
        status: str,
        duration_ms: int,
        summary: dict[str, Any],
    ) -> bool:
        """Send a single node execution as a Langfuse span linked to the turn trace."""
        now_iso = datetime.now(UTC).isoformat()
        span_id = str(uuid4())

        batch = [
            # Ensure the root trace exists
            {
                "id": str(uuid4()),
                "timestamp": now_iso,
                "type": "trace-create",
                "body": {
                    "id": str(trace_id),
                    "name": f"turn:{node}" if node == "load_ctx" else "customer_turn",
                    "sessionId": str(conversation_id) if conversation_id else None,
                    "userId": str(client_id),
                    "metadata": {
                        "client_id": str(client_id),
                        "conversation_id": str(conversation_id) if conversation_id else None,
                    },
                },
            },
            # Record the span for the executed node
            {
                "id": str(uuid4()),
                "timestamp": now_iso,
                "type": "span-create",
                "body": {
                    "id": span_id,
                    "traceId": str(trace_id),
                    "name": f"{agent}:{node}" if agent else node,
                    "startTime": now_iso,
                    "endTime": now_iso,
                    "statusMessage": status,
                    "level": "ERROR" if status == "error" else "DEFAULT",
                    "input": {"node": node, "agent": agent},
                    "output": summary,
                    "metadata": {
                        "duration_ms": duration_ms,
                        "client_id": str(client_id),
                    },
                },
            },
        ]

        return await self._post_batch(batch)

    async def send_test_trace(
        self,
        *,
        name: str = "csa-test-verification",
        user_id: str = "admin",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Send a lightweight test trace to verify connectivity and trigger Langfuse setup."""
        now_iso = datetime.now(UTC).isoformat()
        test_trace_id = str(uuid4())
        test_span_id = str(uuid4())

        batch = [
            {
                "id": str(uuid4()),
                "timestamp": now_iso,
                "type": "trace-create",
                "body": {
                    "id": test_trace_id,
                    "name": name,
                    "userId": user_id,
                    "metadata": metadata or {"source": "csa_verification", "status": "active"},
                    "tags": ["verification", "csa-rebuild"],
                },
            },
            {
                "id": str(uuid4()),
                "timestamp": now_iso,
                "type": "span-create",
                "body": {
                    "id": test_span_id,
                    "traceId": test_trace_id,
                    "name": "system-readiness-probe",
                    "startTime": now_iso,
                    "endTime": now_iso,
                    "statusMessage": "ok",
                    "input": {"probe": "health_check", "engine": "LangGraph"},
                    "output": {"status": "ready", "csa_runtime": "active"},
                },
            },
        ]

        ok = await self._post_batch(batch)
        return ok, test_trace_id

    async def _post_batch(self, batch: list[dict[str, Any]]) -> bool:
        url = f"{self._base_url}/api/public/ingestion"
        headers = {
            "Authorization": self._auth_header,
            "Content-Type": "application/json",
            "x-langfuse-sdk-name": "csa-python",
            "x-langfuse-sdk-version": "1.0.0",
        }
        try:
            resp = await self._http.post(url, json={"batch": batch}, headers=headers)
            if resp.status_code in (200, 201, 207):
                logger.info(
                    "langfuse_batch_ingested",
                    events_count=len(batch),
                    status=resp.status_code,
                )
                return True
            logger.error(
                "langfuse_ingestion_rejected",
                status=resp.status_code,
                body=resp.text[:200],
            )
            return False
        except httpx.HTTPError as exc:
            logger.error("langfuse_connection_failed", error=str(exc))
            return False
