"""Behaviour specific to the local-disk backend."""

import stat
from pathlib import Path
from uuid import uuid4

import pytest
from csa.storage.base import StorageError, StorageNotFound
from csa.storage.local import LocalObjectStore

TENANT = uuid4()


def test_tenant_directory_is_created_with_mode_0700(tmp_path: Path) -> None:
    root = tmp_path / "uploads"
    LocalObjectStore(root).put(TENANT, "faq.csv", b"data")

    assert stat.S_IMODE((root / str(TENANT)).stat().st_mode) == 0o700
    assert stat.S_IMODE((root / str(TENANT) / "faq.csv").stat().st_mode) == 0o600


def test_symlinked_tenant_directory_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "uploads"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / str(TENANT)).symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(StorageError):
        LocalObjectStore(root).put(TENANT, "faq.csv", b"data")
    assert list(elsewhere.iterdir()) == []


def test_read_accepts_an_absolute_path_inside_the_tenant_directory(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "uploads")
    store.put(TENANT, "faq.csv", b"data")
    absolute = (tmp_path / "uploads" / str(TENANT) / "faq.csv").resolve()

    assert store.read(TENANT, str(absolute), 10) == b"data"
    with store.open(TENANT, str(absolute)) as stream:
        assert stream.read() == b"data"


def test_read_refuses_a_path_outside_the_tenant_directory(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "uploads")
    store.put(TENANT, "faq.csv", b"data")
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"secret")

    with pytest.raises(StorageNotFound, match="outside the client upload directory"):
        store.read(TENANT, str(outside), 10)
    with pytest.raises(StorageNotFound, match="outside the client upload directory"):
        store.open(TENANT, str(outside))


def test_list_names_includes_part_files(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "uploads")
    store.put(TENANT, "faq.csv", b"data")
    (tmp_path / "uploads" / str(TENANT) / ".stale.csv.part").write_bytes(b"half")

    assert store.list_names(TENANT) == [".stale.csv.part", "faq.csv"]


def test_delete_removes_the_file(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "uploads")
    store.put(TENANT, "faq.csv", b"data")

    store.delete(TENANT, "faq.csv")

    assert not (tmp_path / "uploads" / str(TENANT) / "faq.csv").exists()


def test_check_passes_when_the_root_does_not_exist_yet(tmp_path: Path) -> None:
    LocalObjectStore(tmp_path / "uploads").check()


def test_check_passes_for_an_existing_directory(tmp_path: Path) -> None:
    LocalObjectStore(tmp_path).check()


def test_check_fails_when_the_root_is_a_file(tmp_path: Path) -> None:
    root = tmp_path / "uploads"
    root.write_bytes(b"not a directory")

    with pytest.raises(StorageError):
        LocalObjectStore(root).check()
