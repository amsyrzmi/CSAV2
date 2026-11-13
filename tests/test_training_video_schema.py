"""Schema contract for migrations 0600 and 0601: video in the training library (the widened
training_items and the six video tables; disposable database).
"""

from __future__ import annotations

import hashlib
import typing
import uuid
from pathlib import Path
from typing import Any, LiteralString

import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION
from psycopg import errors
from scripts import migrate

from tests.conftest import requires_docker
from tests.test_bookings_schema import _app, _owner
from tests.test_training_schema import _company, _item

pytestmark = requires_docker

MIGRATIONS = ("0600", "0601")
OTHER_ROLES = ("csa_ro", "csa_identity", "csa_hr_maintenance", "public")
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")
CHILD_TABLES = (
    "training_video_uploads",
    "training_video_pieces",
    "training_video_jobs",
    "training_transcript_segments",
    "training_transcript_windows",
)
TABLES = (*CHILD_TABLES, "training_video_usage")
ZERO_VECTOR = "[" + ",".join(["0"] * 384) + "]"
DIGEST = hashlib.sha256(b"synthetic piece").hexdigest()

# What each table needs besides (client_id, item_id); a test overrides single columns.
BASE: dict[str, dict[str, Any]] = {
    "training_video_uploads": {"piece_size": 5242880, "piece_count": 3, "declared_bytes": 15000000},
    "training_video_pieces": {"number": 1, "byte_size": 5242880, "sha256": DIGEST, "etag": "e1"},
    "training_video_jobs": {"stage": "transcribing", "status": "queued"},
    "training_transcript_segments": {
        "chunk_index": 0,
        "position": 0,
        "start_ms": 0,
        "end_ms": 1500,
        "text": "Welcome to the induction.",
    },
    "training_transcript_windows": {
        "position": 0,
        "start_ms": 0,
        "end_ms": 30000,
        "text": "Welcome to the induction.",
        "embedding": ZERO_VECTOR,
        "embed_model": "synthetic-embedder",
    },
    "training_video_usage": {"seconds": 90},
}


def _q(text: str) -> LiteralString:
    """A query built from fixed table names above, for the type checker."""
    return typing.cast("LiteralString", text)


def _migration_texts() -> list[str]:
    paths = {v: p for v, p in migrate.discover_migrations()}
    return [Path(paths[v]).read_text(encoding="utf-8") for v in MIGRATIONS]


def _rerun(dsn: str) -> None:
    with _owner(dsn) as conn:
        for text in _migration_texts():
            conn.execute(text)  # type: ignore[arg-type]


def _video(dsn: str, client_id: str, **extra: Any) -> str:
    """A video item in the given company; returns its id."""
    row: dict[str, Any] = {
        "kind": "video",
        "view_mode": None,
        "page_count": None,
        "container": "mp4",
        "media_type": "video/mp4",
        "storage_name": f"{uuid.uuid4()}.mp4",
        "original_name": "induction.mp4",
        **extra,
    }
    sql, params = _item(client_id, **row)
    with _owner(dsn) as conn:
        [made] = conn.execute(_q(sql + " RETURNING id"), params)
    return str(made["id"])


def _row(table: str, client_id: str, item_id: str, **extra: Any) -> tuple[Any, ...]:
    row = {"client_id": client_id, "item_id": item_id, **BASE[table], **extra}
    slots = ["%s::vector" if k == "embedding" else "%s" for k in row]
    return (
        f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join(slots)})",
        tuple(row.values()),
    )


def _seed(dsn: str, table: str, client_id: str) -> str:
    """One row of the table for the company, on a fresh video item; returns the item id."""
    item = _video(dsn, client_id) if table in CHILD_TABLES else str(uuid.uuid4())
    with _owner(dsn) as conn:
        conn.execute(*_row(table, client_id, item))
    return item


@pytest.mark.parametrize("table", TABLES)
def test_new_tables_are_row_secured_not_forced_and_granted_to_csa_app_only(
    migrated_dsn: str, table: str
) -> None:
    with _owner(migrated_dsn) as conn:
        [flags] = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            (f"public.{table}",),
        )
        assert flags == {"relrowsecurity": True, "relforcerowsecurity": False}
        policies = conn.execute(
            "SELECT policyname, qual, with_check FROM pg_policies WHERE tablename = %s", (table,)
        ).fetchall()
        assert [p["policyname"] for p in policies] == ["tenant_isolation"]
        assert "app.client_id" in policies[0]["qual"]
        assert "app.client_id" in policies[0]["with_check"]
        for privilege in PRIVILEGES:
            [app] = conn.execute(
                "SELECT has_table_privilege('csa_app', %s, %s) AS ok",
                (f"public.{table}", privilege),
            )
            assert app["ok"] is True, privilege
            for role in OTHER_ROLES:
                [other] = conn.execute(
                    "SELECT has_table_privilege(%s, %s, %s) AS ok",
                    (role, f"public.{table}", privilege),
                )
                assert other["ok"] is False, (role, privilege)


@pytest.mark.parametrize("table", TABLES)
def test_company_b_cannot_see_or_write_company_a_video_rows(
    migrated_dsn: str, app_dsn: str, table: str
) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    item_a = _seed(migrated_dsn, table, a)
    _seed(migrated_dsn, table, b)
    with _app(app_dsn, b) as conn:
        rows = conn.execute(_q(f"SELECT client_id FROM {table}")).fetchall()
        assert {str(r["client_id"]) for r in rows} == {b}
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(*_row(table, a, item_a, **_other_key(table)))
        conn.rollback()
        sql = _q(f"UPDATE {table} SET client_id = client_id WHERE client_id = %s")
        changed = conn.execute(sql, (a,))
        assert changed.rowcount == 0
        assert conn.execute(_q(f"DELETE FROM {table} WHERE client_id = %s"), (a,)).rowcount == 0
        conn.rollback()
    with _app(app_dsn, None) as conn:
        assert conn.execute(_q(f"SELECT 1 FROM {table}")).fetchall() == []
    with _owner(migrated_dsn) as conn:
        [left] = conn.execute(_q(f"SELECT count(*) AS n FROM {table} WHERE client_id = %s"), (a,))
        assert left["n"] == 1


def _other_key(table: str) -> dict[str, Any]:
    """A second row's key for the same item, so the refusal comes from row security alone."""
    keys: dict[str, dict[str, Any]] = {
        "training_video_pieces": {"number": 2},
        "training_transcript_segments": {"position": 1},
        "training_transcript_windows": {"position": 1},
    }
    return keys.get(table, {})


@pytest.mark.parametrize("table", CHILD_TABLES)
def test_child_row_cannot_point_at_another_companys_item(migrated_dsn: str, table: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    item_a = _video(migrated_dsn, a)
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_row(table, b, item_a))
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_row(table, a, str(uuid.uuid4())))
        conn.execute(*_row(table, a, item_a))


def test_pdf_rows_written_before_0600_stay_valid(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_item(c))
    _rerun(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute(
            "SELECT kind, status, view_mode, page_count, container FROM training_items"
            " WHERE client_id = %s",
            (c,),
        )
        assert row["kind"] == "pdf" and row["status"] == "ready"
        assert row["view_mode"] == "slides" and row["page_count"] == 3
        assert row["container"] is None
        constraints = conn.execute(
            "SELECT conname, convalidated FROM pg_constraint"
            " WHERE conrelid = 'public.training_items'::regclass AND contype = 'c'"
        ).fetchall()
        names = {r["conname"] for r in constraints}
        assert {
            "training_items_kind_chk",
            "training_items_shape_chk",
            "training_items_pdf_ready_chk",
            "training_items_sha256_chk",
        } <= names
        assert all(r["convalidated"] for r in constraints), constraints
        # The old kind check is gone: nothing else constrains kind on its own.
        [only] = conn.execute(
            "SELECT count(*) AS n FROM pg_constraint c JOIN pg_attribute a"
            " ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]"
            " WHERE c.conrelid = 'public.training_items'::regclass AND c.contype = 'c'"
            " AND array_length(c.conkey, 1) = 1 AND a.attname = 'kind'"
        )
        assert only["n"] == 1


def test_video_shape_checks(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    video: dict[str, Any] = {
        "kind": "video",
        "view_mode": None,
        "page_count": None,
        "container": "mp4",
        "media_type": "video/mp4",
    }
    bad: list[dict[str, Any]] = [
        {**video, "view_mode": "slides"},
        {**video, "container": None},
        {**video, "page_count": 3},
        {**video, "sha256": None, "status": "ready"},
        {**video, "container": "avi"},
        {**video, "speech": "maybe"},
        {**video, "status": "paused"},
        {**video, "duration_ms": 0},
        {**video, "transcript_language": "x" * 17},
        {**video, "failure_code": "x" * 65},
        {**video, "video_codec": "x" * 33},
        {**video, "audio_codec": "x" * 33},
        {"status": "uploading"},  # a pdf may not be anything but ready
        {"container": "mp4"},  # a pdf has no container
        {"view_mode": None},
        {"page_count": None},
    ]
    with _owner(migrated_dsn) as conn:
        for extra in bad:
            sql, params = _item(c, storage_name=f"{uuid.uuid4()}.bin", **extra)
            with pytest.raises(errors.CheckViolation):
                conn.execute(sql, params)
        started = {**video, "sha256": None, "status": "uploading"}
        conn.execute(*_item(c, **started))
        full = {
            **video,
            "status": "ready",
            "speech": "speech",
            "duration_ms": 61000,
            "transcript_language": "ms",
            "video_codec": "h264",
            "audio_codec": "aac",
        }
        conn.execute(*_item(c, **full))
        for speech in ("no_speech", "no_audio"):
            conn.execute(*_item(c, **{**video, "container": "webm", "speech": speech}))
        [stored] = conn.execute(
            "SELECT view_mode, page_count, sha256 FROM training_items"
            " WHERE client_id = %s AND status = 'uploading'",
            (c,),
        )
        assert stored == {"view_mode": None, "page_count": None, "sha256": None}


@pytest.mark.parametrize(
    "table, bad",
    [
        ("training_video_uploads", {"piece_size": 5242879}),
        ("training_video_uploads", {"piece_count": 0}),
        ("training_video_uploads", {"piece_count": 10001}),
        ("training_video_uploads", {"declared_bytes": 0}),
        ("training_video_uploads", {"declared_bytes": 524288001}),
        ("training_video_pieces", {"number": 0}),
        ("training_video_pieces", {"number": 10001}),
        ("training_video_pieces", {"byte_size": 0}),
        ("training_video_pieces", {"byte_size": 8388609}),
        ("training_video_pieces", {"sha256": "ABC"}),
        ("training_video_pieces", {"etag": "x" * 129}),
        ("training_video_jobs", {"stage": "indexing"}),
        ("training_video_jobs", {"status": "paused"}),
        ("training_video_jobs", {"attempts": -1}),
        ("training_video_jobs", {"error_code": "x" * 65}),
        ("training_transcript_segments", {"end_ms": -1, "start_ms": -5}),
        ("training_transcript_segments", {"start_ms": 2000}),
        ("training_transcript_segments", {"text": ""}),
        ("training_transcript_segments", {"text": "x" * 2001}),
        ("training_transcript_windows", {"end_ms": -1, "start_ms": 0}),
        ("training_transcript_windows", {"text": ""}),
        ("training_transcript_windows", {"text": "x" * 8001}),
        ("training_video_usage", {"seconds": 0}),
        ("training_video_usage", {"seconds": 3601}),
    ],
)
def test_child_table_checks_refuse_bad_values(
    migrated_dsn: str, table: str, bad: dict[str, Any]
) -> None:
    c = _company(migrated_dsn)
    item = _video(migrated_dsn, c) if table in CHILD_TABLES else str(uuid.uuid4())
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_row(table, c, item, **bad))
        conn.execute(*_row(table, c, item))


def test_job_defaults(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    item = _video(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_row("training_video_jobs", c, item))
        [job] = conn.execute(
            "SELECT attempts, chunks_total, chunks_done, parts, locked_at,"
            " next_attempt_at IS NOT NULL AS due FROM training_video_jobs WHERE client_id = %s",
            (c,),
        )
        assert job == {
            "attempts": 0,
            "chunks_total": None,
            "chunks_done": 0,
            "parts": [],
            "locked_at": None,
            "due": True,
        }


def test_usage_is_unique_per_item_and_has_no_item_foreign_key(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    item = str(uuid.uuid4())
    with _owner(migrated_dsn) as conn:
        conn.execute(*_row("training_video_usage", c, item))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_row("training_video_usage", c, item))
        [fks] = conn.execute(
            "SELECT count(*) AS n FROM pg_constraint"
            " WHERE conrelid = 'public.training_video_usage'::regclass AND contype = 'f'"
        )
        assert fks["n"] == 1  # clients only


def test_deleting_an_item_cascades_children_and_keeps_usage(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    item = _video(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        for table in TABLES:
            conn.execute(*_row(table, c, item))
        conn.execute("DELETE FROM training_items WHERE id = %s", (item,))
        for table in CHILD_TABLES:
            [left] = conn.execute(
                _q(f"SELECT count(*) AS n FROM {table} WHERE client_id = %s"), (c,)
            )
            assert left["n"] == 0, table
        [usage] = conn.execute(
            "SELECT count(*) AS n FROM training_video_usage WHERE client_id = %s", (c,)
        )
        assert usage["n"] == 1


def _shape(dsn: str) -> dict[str, Any]:
    query = (
        "SELECT"
        " (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public'"
        "   AND table_name = ANY(%(t)s)) AS columns,"
        " (SELECT count(*) FROM pg_constraint"
        "   WHERE conrelid = ANY(%(r)s::regclass[])) AS constraints,"
        " (SELECT count(*) FROM pg_constraint WHERE conrelid = ANY(%(r)s::regclass[])"
        "   AND convalidated) AS validated,"
        " (SELECT count(*) FROM pg_indexes WHERE schemaname = 'public'"
        "   AND tablename = ANY(%(t)s)) AS indexes,"
        " (SELECT count(*) FROM pg_policies WHERE tablename = ANY(%(t)s)) AS policies,"
        " (SELECT count(*) FROM pg_class WHERE relname = ANY(%(t)s)"
        "   AND relrowsecurity AND NOT relforcerowsecurity) AS secured"
    )
    names = ["training_items", *TABLES]
    params = {"t": names, "r": [f"public.{n}" for n in names]}
    with _owner(dsn) as conn:
        row = conn.execute(query, params).fetchone()  # type: ignore[arg-type]
    assert row is not None
    return dict(row)


def test_running_0600_and_0601_again_changes_nothing(migrated_dsn: str) -> None:
    before = _shape(migrated_dsn)
    _rerun(migrated_dsn)
    _rerun(migrated_dsn)
    assert _shape(migrated_dsn) == before
    assert before["policies"] == 7 and before["secured"] == 7
    assert before["constraints"] == before["validated"]


def test_video_migrations_touch_no_other_lane() -> None:
    forbidden = (
        "payment",
        "console_proposals_kind_check",
        "console_workspace_turns_action_check",
        "console_workspace_turns_page_key_check",
    )
    for text in _migration_texts():
        lowered = text.lower()
        for word in forbidden:
            assert word not in lowered, word


def test_max_applied_version_is_the_expected_one(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute("SELECT max(version) AS v FROM schema_migrations")
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    assert row["v"] == EXPECTED_SCHEMA_VERSION
    assert {"0600", "0601"} <= applied
