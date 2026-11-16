"""The image reply message: written before the text, its shape, its channels, and the history."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from csa.contracts.envelope import Channel, Envelope
from csa.contracts.facts import PictureRef
from csa.contracts.state import Draft, OutboundMessage, TurnState
from csa.core import db
from csa.core.tenancy import tx
from csa.graph.coordinator import _reply_messages
from csa.media import service
from csa.media.attachments import image_asset_id, image_attachments
from csa.media.images import check_image
from csa.turn.deps import PostgresTurnDependencies, _insert_outbound
from psycopg.types.json import Jsonb

from tests.conftest import requires_docker
from tests.media_images import png
from tests.test_console_inbox import seed
from tests.test_console_sessions import SessionWorld

pytest_plugins = ["tests.test_console_sessions"]

CAPTION = "DuitNow QR: Synthetic Kedai"
TEXT = "Scan the QR to pay, then send us the receipt."


def test_image_attachments_shape_and_reading_it_back() -> None:
    asset = uuid4()
    assert image_attachments(asset) == [{"type": "image", "asset_id": str(asset)}]
    assert image_asset_id(image_attachments(asset)) == asset


@pytest.mark.parametrize(
    "attachments",
    [
        None,
        [],
        "image",
        {"type": "image", "asset_id": "00000000-0000-0000-0000-000000000001"},
        [{"type": "image"}],
        [{"type": "image", "asset_id": "not-a-uuid"}],
        [{"type": "image", "asset_id": 7}],
        [{"type": "file", "asset_id": "00000000-0000-0000-0000-000000000001"}],
        # An inbound photo is stored with `kind`, never `type`: it is not an image reply.
        [{"kind": "image", "mime_type": "image/jpeg", "file_id": "synthetic-file"}],
        ["00000000-0000-0000-0000-000000000001"],
    ],
)
def test_anything_else_is_not_an_image_message(attachments: object) -> None:
    assert image_asset_id(attachments) is None


async def a_picture(w: SessionWorld, client: UUID, subject: str = "method-1") -> UUID:
    data = png(800, 800)
    async with tx(w.pool, str(client)) as conn:
        stored = await service.put(
            conn,
            client,
            "payment_qr",
            subject,
            data,
            check_image(data, None),
            source="upload",
            actor_id=None,
        )
    assert stored is not None
    return stored.id


def image(
    env: Envelope, *, subject: str = "method-1", channel: Channel | None = None
) -> OutboundMessage:
    assert env.contact.id is not None
    return OutboundMessage(
        trace_id=env.trace_id,
        contact_id=env.contact.id,
        channel=channel or env.channel,
        kind="image",
        body=CAPTION,
        picture=PictureRef(kind="payment_qr", subject_key=subject, caption=CAPTION),
        idempotency_key=f"{env.trace_id}:0:picture",
    )


def text(env: Envelope, *, channel: Channel | None = None) -> OutboundMessage:
    assert env.contact.id is not None
    return OutboundMessage(
        trace_id=env.trace_id,
        contact_id=env.contact.id,
        channel=channel or env.channel,
        body=TEXT,
        idempotency_key=f"{env.trace_id}:0",
    )


async def emit(w: SessionWorld, env: Envelope, *messages: OutboundMessage) -> None:
    """The messages written in one transaction, as the turn's emit writes them."""
    async with tx(w.pool, str(env.client_id)) as conn:
        for message in messages:
            await _insert_outbound(conn, env, message)


async def outbound(w: SessionWorld, trace: UUID) -> list[dict[str, Any]]:
    return await w.q(
        "SELECT m.id, m.idempotency_key, m.body, m.redacted_body, m.attachments,"
        " m.has_attachment, m.ui, m.created_at, d.id AS delivery_id,"
        " d.created_at AS delivery_created_at"
        " FROM messages m LEFT JOIN outbound_deliveries d ON d.message_id = m.id"
        " WHERE m.trace_id = %s AND m.direction = 'outbound'"
        " ORDER BY m.created_at, m.id",
        trace,
    )


@requires_docker
async def test_image_message_is_written_before_the_text(sessions: SessionWorld) -> None:
    w = sessions
    env, conversation = await seed(w)
    await a_picture(w, env.client_id)
    await emit(w, env, image(env), text(env))
    first, second = await outbound(w, env.trace_id)
    assert first["idempotency_key"] == f"{env.trace_id}:0:picture"
    assert second["idempotency_key"] == f"{env.trace_id}:0"
    # Strictly earlier on both tables, though both rows were written in one transaction.
    assert first["created_at"] < second["created_at"]
    assert first["delivery_created_at"] < second["delivery_created_at"]

    # The delivery claim takes the image first and holds the text behind it.
    async with await psycopg.AsyncConnection.connect(w.owner_dsn) as owner:
        await owner.execute(
            "UPDATE outbound_deliveries SET next_attempt_at = now() + interval '1 day'"
            " WHERE conversation_id <> %s",
            (conversation,),
        )
        claimed = await db.q.fetch_one("delivery/claim.sql", conn=owner)
        assert claimed is not None and claimed["message_id"] == first["id"]
        assert await db.q.fetch_one("delivery/claim.sql", conn=owner) is None
        await owner.rollback()


@requires_docker
async def test_emit_writes_the_image_first_whatever_the_outbound_order(
    sessions: SessionWorld,
) -> None:
    w = sessions
    env, _ = await seed(w)
    await a_picture(w, env.client_id)
    state = TurnState(envelope=env, outbound=[text(env), image(env)])
    await PostgresTurnDependencies(w.pool).emit(state)
    first, second = await outbound(w, env.trace_id)
    assert first["idempotency_key"] == f"{env.trace_id}:0:picture"
    assert first["created_at"] < second["created_at"]
    assert first["delivery_created_at"] < second["delivery_created_at"]


@requires_docker
async def test_image_message_has_its_attachment_and_caption(sessions: SessionWorld) -> None:
    w = sessions
    env, _ = await seed(w)
    asset = await a_picture(w, env.client_id)
    await emit(w, env, image(env))
    [row] = await outbound(w, env.trace_id)
    assert row["has_attachment"] is True
    assert row["attachments"] == [{"type": "image", "asset_id": str(asset)}]
    assert image_asset_id(row["attachments"]) == asset
    assert row["body"] == row["redacted_body"] == CAPTION
    assert row["ui"] is None
    assert row["delivery_id"] is not None


@requires_docker
async def test_no_asset_means_no_image_message(sessions: SessionWorld) -> None:
    w = sessions
    env, _ = await seed(w)
    await a_picture(w, env.client_id, "method-1")
    await emit(w, env, image(env, subject="method-without-a-picture"), text(env))
    rows = await outbound(w, env.trace_id)
    assert [row["idempotency_key"] for row in rows] == [f"{env.trace_id}:0"]
    assert rows[0]["has_attachment"] is False and rows[0]["delivery_id"] is not None


@requires_docker
@pytest.mark.parametrize("channel", [Channel.WHATSAPP, Channel.WEB])
async def test_image_message_on_another_channel_is_not_written(
    sessions: SessionWorld, channel: Channel
) -> None:
    w = sessions
    seeded, _ = await seed(w)
    env = seeded.model_copy(update={"channel": channel})
    await a_picture(w, env.client_id)
    await emit(w, env, image(env, channel=channel), text(env, channel=channel))
    rows = await outbound(w, env.trace_id)
    assert [row["idempotency_key"] for row in rows] == [f"{env.trace_id}:0"]
    assert rows[0]["body"] == TEXT and rows[0]["has_attachment"] is False


@requires_docker
async def test_resumed_emit_writes_nothing_twice(sessions: SessionWorld) -> None:
    w = sessions
    env, _ = await seed(w)
    await a_picture(w, env.client_id)
    await emit(w, env, image(env), text(env))
    before = await outbound(w, env.trace_id)
    await emit(w, env, image(env), text(env))
    after = await outbound(w, env.trace_id)
    assert len(after) == 2
    assert [(r["id"], r["delivery_id"]) for r in after] == [
        (r["id"], r["delivery_id"]) for r in before
    ]


@requires_docker
async def test_history_leaves_out_outbound_pictures_but_keeps_inbound_attachments(
    sessions: SessionWorld,
) -> None:
    w = sessions
    env, conversation = await seed(w)
    await a_picture(w, env.client_id)
    await emit(w, env, image(env), text(env))
    photo_trace = uuid4()
    async with tx(w.pool, str(env.client_id)) as conn:
        await db.q.fetch_one(
            "worker/insert_message.sql",
            (
                env.client_id,
                conversation,
                "inbound",
                "contact",
                "Here is my receipt",
                "Here is my receipt",
                Jsonb([{"kind": "image", "mime_type": "image/jpeg", "file_id": "synthetic-file"}]),
                True,
                f"{photo_trace}:in",
                photo_trace,
            ),
            conn=conn,
        )
    # Read as a later turn would (another trace), so the reply's own trace hides nothing.
    async with tx(w.pool, str(env.client_id)) as conn:
        history = await db.q.fetch_all(
            "turn/recent_messages.sql",
            {
                "client_id": env.client_id,
                "conversation_id": conversation,
                "trace_id": uuid4(),
                "limit": 20,
            },
            conn=conn,
        )
    assert [(m["direction"], m["redacted_body"]) for m in history] == [
        ("inbound", "Synthetic question"),
        ("outbound", TEXT),
        ("inbound", "Here is my receipt"),
    ]


@requires_docker
async def test_a_handoff_reply_never_lends_its_authority_to_the_image(
    sessions: SessionWorld,
) -> None:
    w = sessions
    env, conversation = await seed(w)
    await a_picture(w, env.client_id)
    # As the escalation agent leaves a handoff turn: an escalation for this trace and the chat
    # muted for a person, so only the handoff reply itself may still be sent.
    await w.q(
        "INSERT INTO escalations (client_id, trace_id, conversation_id, contact_id, reason)"
        " VALUES (%s,%s,%s,%s,'synthetic_handoff')",
        env.client_id,
        env.trace_id,
        conversation,
        env.contact.id,
    )
    await w.q(
        "UPDATE conversations SET status='escalated', owned_by='human_support',"
        " ai_muted_until=now() + interval '1 day' WHERE id=%s",
        conversation,
    )
    state = TurnState(
        envelope=env,
        draft=Draft(text=TEXT, language="en", handoff=True),
        picture=PictureRef(kind="payment_qr", subject_key="method-1", caption=CAPTION),
    )
    # The messages the coordinator's emit builds for this turn, written by the real emit.
    messages = _reply_messages(state)
    assert [m.kind for m in messages] == ["image", "text"]
    await PostgresTurnDependencies(w.pool).emit(state.model_copy(update={"outbound": messages}))
    rows = await w.q(
        "SELECT idempotency_key, has_attachment, send_authority FROM messages"
        " WHERE trace_id=%s AND direction='outbound'",
        env.trace_id,
    )
    assert rows == [
        {
            "idempotency_key": f"{env.trace_id}:0",
            "has_attachment": False,
            "send_authority": "handoff",
        }
    ]
