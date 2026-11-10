"""Schema contract for migration 0550: kb_ingest_jobs gets the 'cancelled' status and dismissed_at,
and the one-time backfill clears the jobs whose files were all removed (disposable database).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from psycopg import errors
from scripts import migrate

from tests.conftest import _owner_dsn, _run_migrations, requires_docker
from tests.test_bookings_bot_schema import _allowed
from tests.test_bookings_schema import _owner
from tests.test_training_schema import _company

pytestmark = requires_docker

CHECK = "kb_ingest_jobs_status_check"
MIGRATION = "0550"
OLD_VALUES = {"queued", "processing", "done", "partial", "failed"}


def _job(client_id: str, files: list[dict[str, Any]], status: str, **extra: Any) -> tuple[Any, ...]:
    row: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "client_id": client_id,
        "source": "upload",
        "status": status,
        "files": json.dumps(files),
        **extra,
    }
    names, slots = ", ".join(row), ", ".join(["%s"] * len(row))
    return f"INSERT INTO kb_ingest_jobs ({names}) VALUES ({slots})", tuple(row.values())


def _stored_file(conn: Any, client_id: str, job_id: str, *, deleted: bool) -> str:
    """A kb_upload_files row (with the batch it needs) for the job; returns the file's id."""
    batch_id, file_id = str(uuid.uuid4()), str(uuid.uuid4())
    conn.execute(
        "INSERT INTO kb_upload_batches (id, client_id, idempotency_key, fingerprint, job_id)"
        " VALUES (%s, %s, %s, 'synthetic', %s)",
        (batch_id, client_id, uuid.uuid4(), job_id),
    )
    conn.execute(
        "INSERT INTO kb_upload_files (id, client_id, batch_id, storage_name, name, media_type,"
        " size_bytes, content_hash, deleted_at)"
        " VALUES (%s, %s, %s, %s, 'synthetic.pdf', 'application/pdf', 1024, 'synthetic',"
        " CASE WHEN %s THEN now() END)",
        (file_id, client_id, batch_id, f"{uuid.uuid4()}.pdf", deleted),
    )
    return file_id


def _add_job(
    conn: Any, client_id: str, status: str, kinds: list[str], **extra: Any
) -> tuple[str, str]:
    """Insert a job whose entries are of the given kinds: 'deleted' and 'live' point at a
    kb_upload_files row, 'path' has a path and no file_id, 'unknown' names a file_id with no row.
    """
    sql, params = _job(client_id, [], status, **extra)
    conn.execute(sql, params)
    job_id = params[0]
    files: list[dict[str, Any]] = []
    for kind in kinds:
        entry: dict[str, Any] = {"path": f"/synthetic/{uuid.uuid4()}.pdf", "name": "synthetic.pdf"}
        if kind in ("deleted", "live"):
            entry["file_id"] = _stored_file(conn, client_id, job_id, deleted=kind == "deleted")
        elif kind == "unknown":
            entry["file_id"] = str(uuid.uuid4())
        files.append(entry)
    conn.execute("UPDATE kb_ingest_jobs SET files = %s WHERE id = %s", (json.dumps(files), job_id))
    return job_id, status


@dataclass
class Backfilled:
    dsn: str
    jobs: dict[str, tuple[str, str]]  # scenario -> (job id, status before 0550)


@pytest.fixture(scope="module")
def backfilled(pg_container: Any) -> Iterator[Backfilled]:
    """A database migrated to 0542 only, holding one job per scenario, then migrated to 0550."""
    owner = _owner_dsn(pg_container)
    with _owner(owner) as conn:
        conn.execute("CREATE DATABASE before_0550 OWNER csa")
    from tests.conftest import SCHEMA_BASELINE

    pg_container.copy_into_container(SCHEMA_BASELINE.read_bytes(), "/tmp/before_baseline.sql")
    code, output = pg_container.exec(
        [
            "psql",
            "-U",
            "csa",
            "-d",
            "before_0550",
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
            "-f",
            "/tmp/before_baseline.sql",
        ]
    )
    assert code == 0, output.decode(errors="replace")
    dsn = owner.rsplit("/", 1)[0] + "/before_0550"

    real = migrate.discover_migrations
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            migrate, "discover_migrations", lambda: [m for m in real() if m[0] < MIGRATION]
        )
        _run_migrations(dsn)
        client = _company(dsn)
        jobs: dict[str, tuple[str, str]] = {}
        with _owner(dsn) as conn:
            for status in ("failed", "partial", "queued", "processing"):
                jobs[f"deleted_{status}"] = _add_job(
                    conn,
                    client,
                    status,
                    ["deleted", "deleted"],
                    locked_at="2031-01-01T00:00Z",
                    attempts=2,
                )
            jobs["one_live"] = _add_job(conn, client, "failed", ["deleted", "live"])
            jobs["path_only"] = _add_job(conn, client, "failed", ["path"])
            jobs["deleted_and_path"] = _add_job(conn, client, "failed", ["deleted", "path"])
            jobs["unknown_file"] = _add_job(conn, client, "failed", ["unknown"])
            jobs["deleted_and_unknown"] = _add_job(conn, client, "failed", ["deleted", "unknown"])
            jobs["no_files"] = _add_job(conn, client, "failed", [])
            jobs["done"] = _add_job(conn, client, "done", ["deleted"])
        patch.setattr(migrate, "discover_migrations", real)
        _run_migrations(dsn)
    yield Backfilled(dsn, jobs)


def _row(dsn: str, job_id: str) -> dict[str, Any]:
    with _owner(dsn) as conn:
        [row] = conn.execute(
            "SELECT status, dismissed_at, locked_at, finished_at, attempts FROM kb_ingest_jobs"
            " WHERE id = %s",
            (job_id,),
        )
    return row


def test_status_check_allows_cancelled_and_keeps_the_old_values(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        assert _allowed(conn, CHECK) == OLD_VALUES | {"cancelled"}
        client = _company(migrated_dsn)
        for status in sorted(OLD_VALUES | {"cancelled"}):
            conn.execute(*_job(client, [], status))
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_job(client, [], "running"))


def test_dismissed_at_is_nullable_and_new_jobs_start_visible(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        [column] = conn.execute(
            "SELECT data_type, is_nullable, column_default FROM information_schema.columns"
            " WHERE table_name = 'kb_ingest_jobs' AND column_name = 'dismissed_at'"
        )
        assert column == {
            "data_type": "timestamp with time zone",
            "is_nullable": "YES",
            "column_default": None,
        }
        sql, params = _job(_company(migrated_dsn), [], "failed")
        conn.execute(sql, params)
        [row] = conn.execute("SELECT dismissed_at FROM kb_ingest_jobs WHERE id = %s", (params[0],))
        assert row["dismissed_at"] is None


@pytest.mark.parametrize("status", ["failed", "partial", "queued", "processing"])
def test_backfill_cancels_failed_partial_queued_and_processing_jobs_whose_files_are_all_deleted(
    backfilled: Backfilled, status: str
) -> None:
    job_id, before = backfilled.jobs[f"deleted_{status}"]
    assert before == status
    row = _row(backfilled.dsn, job_id)
    assert row["status"] == "cancelled"
    assert row["dismissed_at"] is not None
    assert row["locked_at"] is None
    assert row["finished_at"] is not None
    assert row["attempts"] == 2


def test_backfill_leaves_a_job_with_one_live_file(backfilled: Backfilled) -> None:
    job_id, status = backfilled.jobs["one_live"]
    row = _row(backfilled.dsn, job_id)
    assert (row["status"], row["dismissed_at"]) == (status, None)


def test_backfill_leaves_a_path_only_job_alone(backfilled: Backfilled) -> None:
    for scenario in ("path_only", "deleted_and_path"):
        job_id, status = backfilled.jobs[scenario]
        row = _row(backfilled.dsn, job_id)
        assert (row["status"], row["dismissed_at"]) == (status, None), scenario


def test_backfill_keeps_what_it_cannot_prove_is_removed(backfilled: Backfilled) -> None:
    """A file_id with no kb_upload_files row, an empty files list and a finished job all stay."""
    for scenario in ("unknown_file", "deleted_and_unknown", "no_files", "done"):
        job_id, status = backfilled.jobs[scenario]
        row = _row(backfilled.dsn, job_id)
        assert (row["status"], row["dismissed_at"]) == (status, None), scenario


def test_the_migration_is_safe_to_run_twice(backfilled: Backfilled) -> None:
    [path] = [p for v, p in migrate.discover_migrations() if v == MIGRATION]
    with _owner(backfilled.dsn) as conn:
        before = {
            r["id"]: r
            for r in conn.execute("SELECT * FROM kb_ingest_jobs ORDER BY id")  # type: ignore[arg-type]
        }
        [definition] = conn.execute(
            "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname = %s", (CHECK,)
        )
        conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
        after = {
            r["id"]: r
            for r in conn.execute("SELECT * FROM kb_ingest_jobs ORDER BY id")  # type: ignore[arg-type]
        }
        [again] = conn.execute(
            "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname = %s", (CHECK,)
        )
    assert after == before
    assert again == definition
