"""Release inputs must not include local configuration or copied reference trees."""

from pathlib import Path

import pytest
from scripts.release_context import materialize
from scripts.staging_migrate import approved_target


def source_tree(root: Path) -> Path:
    for directory in ("apps", "csa", "scripts", "db/migrations", "db/sql", "deploy"):
        (root / directory).mkdir(parents=True)
    (root / "csa/evals/data").mkdir(parents=True)
    for name in (
        "pyproject.toml",
        "uv.lock",
        "apps/main.py",
        "csa/__init__.py",
        "csa/evals/data/decision_cases.json",
        "scripts/__init__.py",
        "db/schema_baseline.sql",
        "db/migrations/0360_identity.sql",
        "db/sql/query.sql",
        "deploy/Dockerfile",
    ):
        (root / name).write_text("synthetic input")
    return root


def test_context_excludes_environment_local_sql_caches_and_reference(tmp_path: Path) -> None:
    source = source_tree(tmp_path / "source")
    for name in (".env.local", "csa/.env", "db/snapshot.sql", "db/sql/query.local.sql"):
        (source / name).write_text("synthetic excluded input")
    cache = source / "csa/__pycache__"
    cache.mkdir()
    (cache / "cached.py").write_text("synthetic excluded input")
    destination = tmp_path / "context"
    materialize(source, destination)
    files = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}
    assert files == {
        "pyproject.toml",
        "uv.lock",
        "apps/main.py",
        "csa/__init__.py",
        "csa/evals/data/decision_cases.json",
        "scripts/__init__.py",
        "db/schema_baseline.sql",
        "db/migrations/0360_identity.sql",
        "db/sql/query.sql",
        "Dockerfile",
    }
    with pytest.raises(FileExistsError):
        materialize(source, destination)


@pytest.mark.parametrize("name", ["csa/escape.py", "db/sql/escape.sql", "csa/escape"])
def test_context_rejects_symlinks(tmp_path: Path, name: str) -> None:
    source = source_tree(tmp_path / "source")
    (source / name).symlink_to(tmp_path)
    with pytest.raises(ValueError, match="Symlink"):
        materialize(source, tmp_path / "context")


def test_context_rejects_symlinked_dockerfile_parent(tmp_path: Path) -> None:
    source = source_tree(tmp_path / "source")
    (source / "deploy/Dockerfile").unlink()
    (source / "deploy").rmdir()
    (source / "deploy").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="Dockerfile"):
        materialize(source, tmp_path / "context")


def test_context_rejects_destination_inside_source(tmp_path: Path) -> None:
    source = source_tree(tmp_path / "source")
    with pytest.raises(ValueError, match="outside"):
        materialize(source, source / "context")


@pytest.mark.parametrize(
    ("approval", "dsn", "expected"),
    [
        ("approved-isolated-staging", "postgresql://csa@staging-db/csa_staging", True),
        ("approved-isolated-staging", "postgres://csa@staging-db:5432/csa_staging", True),
        (None, "postgresql://csa@staging-db/csa_staging", False),
        ("disabled", "postgresql://csa@staging-db/csa_staging", False),
        ("approved-isolated-staging", "postgresql://csa@postgres/csa", False),
        ("approved-isolated-staging", "postgresql://csa_app@staging-db/csa_staging", False),
        ("approved-isolated-staging", "postgresql://csa@staging-db/csa", False),
        ("approved-isolated-staging", "postgresql://csa@staging-db:5434/csa_staging", False),
        (
            "approved-isolated-staging",
            "postgresql://csa@staging-db/csa_staging?host=postgres",
            False,
        ),
    ],
)
def test_staging_migration_requires_approval_and_exact_isolated_target(
    approval: str | None, dsn: str, expected: bool
) -> None:
    assert approved_target(approval, dsn) is expected


def test_staging_migration_without_approval_never_reads_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.staging_migrate import main

    monkeypatch.delenv("CSA_MIGRATION_APPROVAL", raising=False)

    def forbidden_read(*args: object, **kwargs: object) -> str:
        raise AssertionError("must not read secrets before approval")

    monkeypatch.setattr(Path, "read_text", forbidden_read)
    with pytest.raises(RuntimeError, match="approval required"):
        main()
