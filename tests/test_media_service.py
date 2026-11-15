"""The picture store functions against a real database: replace, generated, the limit, isolation."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest
import pytest_asyncio
from csa.core import db
from csa.core.tenancy import tx
from csa.media import service
from csa.media.images import MAX_PICTURES, ImageInfo, PictureRefused, check_image
from psycopg_pool import AsyncConnectionPool

from tests.conftest import requires_docker
from tests.media_images import jpeg, png
from tests.test_training_schema import _company

pytestmark = [requires_docker, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def pool(app_dsn: str) -> AsyncIterator[AsyncConnectionPool]:
    async with db.build_pool(app_dsn, max_size=2, autocommit=True) as opened:
        yield opened


async def owner_rows(dsn: str, statement: str, *params: Any) -> list[tuple[Any, ...]]:
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        cursor = await conn.execute(statement, params)  # type: ignore[arg-type]
        return await cursor.fetchall() if cursor.description else []


async def store(
    pool: AsyncConnectionPool,
    client: str,
    data: bytes,
    *,
    subject: str = "gold",
    kind: Any = "plan",
    source: Any = "upload",
) -> service.PictureOut | None:
    info = check_image(data, None)
    async with tx(pool, client) as conn:
        return await service.put(
            conn, uuid.UUID(client), kind, subject, data, info, source=source, actor_id=None
        )


def info_for(data: bytes) -> ImageInfo:
    return check_image(data, None)


async def test_put_then_replace_keeps_the_id_and_changes_the_version(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client = _company(migrated_dsn)
    first_bytes, second_bytes = png(800, 800), jpeg(640, 480)
    first = await store(pool, client, first_bytes)
    assert first is not None and first.version == info_for(first_bytes).sha256
    [(stamped,)] = await owner_rows(
        migrated_dsn, "SELECT updated_at FROM media_assets WHERE id = %s", first.id
    )
    second = await store(pool, client, second_bytes)
    assert second is not None
    assert second.id == first.id and second.version != first.version
    assert second.version == info_for(second_bytes).sha256
    async with tx(pool, client) as conn:
        stored = await service.get(conn, uuid.UUID(client), first.id)
        found = await service.picture_for(conn, uuid.UUID(client), "plan", "gold")
    assert stored is not None and stored.data == second_bytes
    assert (stored.kind, stored.subject_key, stored.media_type) == ("plan", "gold", "image/jpeg")
    assert stored.sha256 == second.version and found == second
    [(moved, media_type, size)] = await owner_rows(
        migrated_dsn,
        "SELECT updated_at, media_type, byte_size FROM media_assets WHERE id = %s",
        first.id,
    )
    assert moved > stamped and media_type == "image/jpeg" and size == len(second_bytes)
    [(rows,)] = await owner_rows(
        migrated_dsn, "SELECT count(*) FROM media_asset_data WHERE client_id = %s", client
    )
    assert rows == 1


async def test_generated_never_overwrites(pool: AsyncConnectionPool, migrated_dsn: str) -> None:
    client = _company(migrated_dsn)
    uploaded = await store(pool, client, png(800, 800), kind="training_thumbnail", subject="v1")
    assert uploaded is not None
    again = await store(
        pool, client, jpeg(320, 180), kind="training_thumbnail", subject="v1", source="generated"
    )
    assert again is None
    async with tx(pool, client) as conn:
        stored = await service.get(conn, uuid.UUID(client), uploaded.id)
    assert stored is not None and stored.data == png(800, 800)
    assert stored.sha256 == uploaded.version
    # A new subject takes a generated picture, which an upload may then replace.
    made = await store(
        pool, client, jpeg(320, 180), kind="training_thumbnail", subject="v2", source="generated"
    )
    assert made is not None
    replaced = await store(pool, client, png(300, 300), kind="training_thumbnail", subject="v2")
    assert replaced is not None and replaced.id == made.id
    [(source,)] = await owner_rows(
        migrated_dsn, "SELECT source FROM media_assets WHERE id = %s", made.id
    )
    assert source == "upload"


async def test_three_hundred_uploads_then_picture_limit(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client = _company(migrated_dsn)
    digest = info_for(png(800, 800)).sha256
    await owner_rows(
        migrated_dsn,
        "INSERT INTO media_assets (client_id, kind, subject_key, media_type, byte_size, width,"
        " height, sha256, source) SELECT %s, 'product', 'p' || n, 'image/png', 1000, 800, 800, %s,"
        " 'upload' FROM generate_series(1, %s) AS n",
        client,
        digest,
        MAX_PICTURES,
    )
    with pytest.raises(PictureRefused) as caught:
        await store(pool, client, png(800, 800), kind="product", subject="new")
    assert caught.value.code == "picture_limit"
    # Replacing an existing subject still works.
    replaced = await store(pool, client, jpeg(640, 480), kind="product", subject="p7")
    assert replaced is not None
    # Generated pictures neither count nor are refused by the limit.
    await owner_rows(
        migrated_dsn,
        "INSERT INTO media_assets (client_id, kind, subject_key, media_type, byte_size, width,"
        " height, sha256, source) VALUES (%s, 'training_thumbnail', 'g1', 'image/png', 1000, 800,"
        " 800, %s, 'generated')",
        client,
        digest,
    )
    made = await store(
        pool, client, jpeg(320, 180), kind="training_thumbnail", subject="g2", source="generated"
    )
    assert made is not None
    # An upload to a subject that holds a generated picture would add an upload: refused too.
    with pytest.raises(PictureRefused):
        await store(pool, client, png(300, 300), kind="training_thumbnail", subject="g1")
    # One below the limit, a new upload is accepted.
    await owner_rows(
        migrated_dsn,
        "DELETE FROM media_assets WHERE client_id = %s AND subject_key = 'p1'",
        client,
    )
    assert await store(pool, client, png(800, 800), kind="product", subject="new") is not None
    # Another company is unaffected.
    other = _company(migrated_dsn)
    assert await store(pool, other, png(800, 800), kind="product", subject="new") is not None


async def test_delete_for_removes_asset_and_bytes(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client = _company(migrated_dsn)
    kept = await store(pool, client, png(800, 800), subject="silver")
    gone = await store(pool, client, png(800, 800), subject="gold")
    assert kept is not None and gone is not None
    async with tx(pool, client) as conn:
        assert await service.delete_for(conn, uuid.UUID(client), "plan", "gold") is True
        assert await service.delete_for(conn, uuid.UUID(client), "plan", "gold") is False
        assert await service.get(conn, uuid.UUID(client), gone.id) is None
    rows = await owner_rows(
        migrated_dsn, "SELECT asset_id FROM media_asset_data WHERE client_id = %s", client
    )
    assert rows == [(kept.id,)]
    async with tx(pool, client) as conn:
        assert await service.delete(conn, uuid.UUID(client), kept.id) is True
        assert await service.delete(conn, uuid.UUID(client), kept.id) is False
    assert (
        await owner_rows(migrated_dsn, "SELECT 1 FROM media_assets WHERE client_id=%s", client)
        == []
    )
    assert (
        await owner_rows(migrated_dsn, "SELECT 1 FROM media_asset_data WHERE client_id=%s", client)
        == []
    )


async def test_get_and_picture_for_never_see_another_company(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    made = await store(pool, a, png(800, 800))
    assert made is not None
    for scope in (b, a):  # B's own scope, and A's scope asked for B (the query names the company)
        async with tx(pool, scope) as conn:
            assert await service.get(conn, uuid.UUID(b), made.id) is None
            assert await service.get_meta(conn, uuid.UUID(b), made.id) is None
            assert await service.picture_for(conn, uuid.UUID(b), "plan", "gold") is None
            assert await service.delete(conn, uuid.UUID(b), made.id) is False
            assert await service.delete_for(conn, uuid.UUID(b), "plan", "gold") is False
    # A's scope naming A's company still sees it, and B's scope naming A sees nothing.
    async with tx(pool, b) as conn:
        assert await service.get(conn, uuid.UUID(a), made.id) is None
        assert await service.get_meta(conn, uuid.UUID(a), made.id) is None
        assert await service.picture_for(conn, uuid.UUID(a), "plan", "gold") is None
        assert await service.delete(conn, uuid.UUID(a), made.id) is False
    async with tx(pool, a) as conn:
        assert await service.picture_for(conn, uuid.UUID(a), "plan", "gold") == made
    # B can hold its own picture for the same subject.
    mine = await store(pool, b, jpeg(640, 480))
    assert mine is not None and mine.id != made.id


async def test_get_meta_reads_the_metadata_without_the_bytes(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client = _company(migrated_dsn)
    data = jpeg(640, 480)
    made = await store(pool, client, data, kind="product", subject="p1")
    assert made is not None
    async with tx(pool, client) as conn:
        meta = await service.get_meta(conn, uuid.UUID(client), made.id)
        assert await service.get_meta(conn, uuid.UUID(client), uuid.uuid4()) is None
    assert meta == service.PictureMeta(
        id=made.id,
        kind="product",
        subject_key="p1",
        media_type="image/jpeg",
        sha256=info_for(data).sha256,
    )
    assert not hasattr(meta, "data")


async def test_put_refuses_info_that_does_not_describe_the_data(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client = _company(migrated_dsn)
    data, other = png(800, 800), jpeg(640, 480)
    right = check_image(data, None)
    wrong_size = ImageInfo(
        right.media_type, right.width, right.height, right.byte_size + 1, right.sha256
    )
    wrong_hash = check_image(other, None)
    wrong_hash = ImageInfo(
        right.media_type, right.width, right.height, right.byte_size, wrong_hash.sha256
    )
    for info in (wrong_size, wrong_hash):
        async with tx(pool, client) as conn:
            with pytest.raises(ValueError, match="info_does_not_match_data"):
                await service.put(
                    conn,
                    uuid.UUID(client),
                    "plan",
                    "gold",
                    data,
                    info,
                    source="upload",
                    actor_id=None,
                )
    assert (
        await owner_rows(migrated_dsn, "SELECT 1 FROM media_assets WHERE client_id = %s", client)
        == []
    )
    assert await store(pool, client, data) is not None


async def test_the_company_lock_is_a_transaction_level_hashtextextended_lock(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client = _company(migrated_dsn)
    held = (
        "SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
        " AND ((classid::bigint << 32) | objid::bigint) = hashtextextended(%s, 0)"
    )
    # After commit, any session: the key is unique to this company, so 0 cannot pass vacuously
    # by looking at a different backend than the one that held it.
    anywhere = (
        "SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory'"
        " AND ((classid::bigint << 32) | objid::bigint) = hashtextextended(%s, 0)"
    )
    async with tx(pool, client) as conn:
        data = png(800, 800)
        await service.put(
            conn,
            uuid.UUID(client),
            "plan",
            "gold",
            data,
            check_image(data, None),
            source="upload",
            actor_id=None,
        )
        cursor = await conn.execute(held, (f"{client}:media",))  # type: ignore[arg-type]
        row: Any = await cursor.fetchone()  # the pool's rows are dicts
    assert row is not None and row["n"] == 1
    async with pool.connection() as conn:  # a transaction-level lock is gone after commit
        cursor = await conn.execute(anywhere, (f"{client}:media",))  # type: ignore[arg-type]
        row: Any = await cursor.fetchone()  # the pool's rows are dicts
    assert row is not None and row["n"] == 0
