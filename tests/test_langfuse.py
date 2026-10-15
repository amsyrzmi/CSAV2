"""Tests for Langfuse integration and event sink."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from csa.core.langfuse import LangfuseSink
from csa.graph.events import PostgresEventSink


@pytest.mark.asyncio
async def test_langfuse_sink_send_event_success() -> None:
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_http.post = AsyncMock(return_value=mock_resp)

    sink = LangfuseSink(
        public_key="pk-lf-test",
        secret_key="sk-lf-test",
        base_url="https://jp.cloud.langfuse.com",
        http_client=mock_http,
    )

    client_id = uuid4()
    trace_id = uuid4()
    conv_id = uuid4()

    ok = await sink.send_event(
        client_id=client_id,
        trace_id=trace_id,
        conversation_id=conv_id,
        node="plan_args",
        agent="order_agent",
        status="ok",
        duration_ms=45,
        summary={"intent": "order_status", "confidence": 0.95},
    )

    assert ok is True
    mock_http.post.assert_awaited_once()
    call_args = mock_http.post.await_args
    assert call_args is not None
    url, kwargs = call_args[0][0], call_args[1]
    assert url == "https://jp.cloud.langfuse.com/api/public/ingestion"
    assert "Authorization" in kwargs["headers"]
    batch = kwargs["json"]["batch"]
    assert len(batch) == 2
    assert batch[0]["type"] == "trace-create"
    assert batch[0]["body"]["id"] == str(trace_id)
    assert batch[1]["type"] == "span-create"
    assert batch[1]["body"]["name"] == "order_agent:plan_args"


@pytest.mark.asyncio
async def test_langfuse_sink_send_test_trace() -> None:
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_http.post = AsyncMock(return_value=mock_resp)

    sink = LangfuseSink(
        public_key="pk-lf-test",
        secret_key="sk-lf-test",
        base_url="https://jp.cloud.langfuse.com",
        http_client=mock_http,
    )

    ok, test_trace_id = await sink.send_test_trace(
        name="initialization-probe",
        metadata={"environment": "dev"},
    )

    assert ok is True
    assert isinstance(test_trace_id, str)
    mock_http.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_langfuse_sink_network_error_graceful() -> None:
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    sink = LangfuseSink(
        public_key="pk-lf-test",
        secret_key="sk-lf-test",
        http_client=mock_http,
    )

    ok = await sink.send_event(
        client_id=uuid4(),
        trace_id=uuid4(),
        conversation_id=None,
        node="signals",
        agent=None,
        status="ok",
        duration_ms=10,
        summary={},
    )

    assert ok is False


@pytest.mark.asyncio
async def test_postgres_event_sink_forwards_to_langfuse() -> None:
    mock_pool = MagicMock()
    mock_langfuse = MagicMock(spec=LangfuseSink)
    mock_langfuse.send_event = AsyncMock()

    sink = PostgresEventSink(mock_pool, langfuse_sink=mock_langfuse)

    client_id = uuid4()
    trace_id = uuid4()
    mock_conn = AsyncMock()

    with pytest.MonkeyPatch.context() as m:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_conn
        mock_ctx.__aexit__.return_value = None
        m.setattr("csa.graph.events.tx", MagicMock(return_value=mock_ctx))
        m.setattr("csa.graph.events.db.q.execute", AsyncMock())

        await sink.record(
            client_id=client_id,
            trace_id=trace_id,
            conversation_id=None,
            node="guard",
            agent=None,
            status="ok",
            duration_ms=5,
            summary={"allowed": True},
        )

        mock_langfuse.send_event.assert_awaited_once_with(
            client_id=client_id,
            trace_id=trace_id,
            conversation_id=None,
            node="guard",
            agent=None,
            status="ok",
            duration_ms=5,
            summary={"allowed": True},
        )
