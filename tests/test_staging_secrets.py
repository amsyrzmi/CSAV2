"""Synthetic checks for the staging secret-file generator/checker (tmp dirs only)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from scripts import staging_secrets

from tests.test_staging_compose import SECRET_FILES

UID = os.getuid()


def _create(tmp_path: Path) -> Path:
    target = tmp_path / "secrets"
    staging_secrets.create(target, UID)
    return target


def _values(target: Path) -> list[str]:
    return [(target / name).read_text() for name in staging_secrets.FILES]


def test_layout_matches_compose_secret_sources_except_connector() -> None:
    assert set(staging_secrets.FILES) == SECRET_FILES - {"connector/staging_tunnel_token"}


def test_create_writes_owner_only_consistent_layout(tmp_path: Path) -> None:
    target = _create(tmp_path)
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    for directory in staging_secrets.DIRECTORIES:
        assert stat.S_IMODE((target / directory).stat().st_mode) == 0o700
    for name in staging_secrets.FILES:
        info = (target / name).lstat()
        assert stat.S_ISREG(info.st_mode)
        assert stat.S_IMODE(info.st_mode) == 0o400
        assert info.st_uid == UID
        assert not (target / name).read_text().endswith("\n")
    assert not (target / "connector").exists()
    assert [p.name for p in tmp_path.iterdir()] == ["secrets"]
    assert staging_secrets.check(target, UID) == []


def test_values_are_fresh_and_distinct(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = _create(tmp_path / "a")
    second = _create(tmp_path / "b")
    passwords = [(first / name).read_text() for name in staging_secrets.PASSWORDS]
    assert len(set(passwords)) == len(passwords)
    assert not set(_values(first)) & set(_values(second)) - {staging_secrets.DISABLED_MODEL_KEY}


def test_main_never_prints_values(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "secrets"
    args = ["--secret-dir", str(target), "--runtime-uid", str(UID)]
    assert staging_secrets.main(["create", *args]) == 0
    assert staging_secrets.main(["check", *args]) == 0
    output = capsys.readouterr()
    for value in _values(target):
        assert value not in output.out + output.err
    assert "OK 10 staging secret files" in output.out


@pytest.mark.parametrize("relative", [True, False], ids=["relative", "inside-repo"])
def test_create_refuses_unsafe_targets(relative: bool) -> None:
    target = Path("secrets") if relative else staging_secrets.REPO_ROOT / "staging-secrets"
    with pytest.raises(staging_secrets.ProvisionError):
        staging_secrets.create(target, UID)
    assert not (staging_secrets.REPO_ROOT / "staging-secrets").exists()


def test_create_refuses_existing_directory_and_wrong_uid(tmp_path: Path) -> None:
    target = _create(tmp_path)
    before = _values(target)
    with pytest.raises(staging_secrets.ProvisionError, match="already exists"):
        staging_secrets.create(target, UID)
    assert _values(target) == before
    with pytest.raises(staging_secrets.ProvisionError, match="runtime UID"):
        staging_secrets.create(tmp_path / "other", UID + 1)
    assert not (tmp_path / "other").exists()


def _rewrite(path: Path, value: str) -> None:
    path.chmod(0o600)
    path.write_text(value)
    path.chmod(0o400)


def test_check_reports_tampering_without_values(tmp_path: Path) -> None:
    target = _create(tmp_path)
    owner = (target / "database/staging_owner_password").read_text()
    (target / "runtime/staging_domain_dsn").chmod(0o440)
    _rewrite(
        target / "runtime/staging_identity_dsn",
        f"postgresql://csa_identity:{'x' * 48}@staging-db:5432/csa_staging",
    )
    _rewrite(
        target / "migration/staging_owner_dsn",
        f"postgresql://csa:{owner}@prod-db:5432/csa",
    )
    _rewrite(target / "runtime/staging_credentials_key", "base64:c2hvcnQ=")
    (target / "runtime/extra").write_text("x")
    failures = staging_secrets.check(target, UID)
    assert failures == [
        "runtime/staging_domain_dsn: mode is not 0400",
        "runtime/extra: unexpected entry",
        "migration/staging_owner_dsn: must be csa@staging-db:5432/csa_staging",
        "runtime/staging_identity_dsn: password does not match migration/staging_identity_password",
        "runtime/staging_credentials_key: not base64 of 32 bytes",
    ]
    assert owner not in "\n".join(failures)


def test_check_rejects_symlink_and_loose_directory(tmp_path: Path) -> None:
    target = _create(tmp_path)
    key = target / "runtime/staging_disabled_model_key"
    key.chmod(0o600)
    key.unlink()
    key.symlink_to(target / "database/staging_owner_password")
    assert staging_secrets.check(target, UID) == [
        "runtime/staging_disabled_model_key: not a regular file"
    ]
    (target / "runtime").chmod(0o755)
    assert staging_secrets.check(target, UID) == ["runtime: mode is not 0700"]


def test_generated_runtime_values_satisfy_settings(tmp_path: Path) -> None:
    from csa.core.settings import Settings

    target = _create(tmp_path)
    no_mounts = tmp_path / "no-mounted-secrets"
    no_mounts.mkdir()

    def read(name: str) -> str:
        return (target / f"runtime/staging_{name}").read_text()

    values = {
        "ENV": "prod",
        "DATABASE_URL": read("domain_dsn"),
        "LG_DATABASE_URL": read("checkpoint_dsn"),
        "IDENTITY_DATABASE_URL": read("identity_dsn"),
        "CREDENTIALS_KEY": read("credentials_key"),
        "GROQ_API_KEY": read("disabled_model_key"),
        "CONSOLE_ALLOWED_ORIGIN": "https://staging.invalid",
        "PUBLIC_BASE_URL": "https://staging.invalid",
    }
    settings = Settings(_env_file=None, _secrets_dir=no_mounts, **values)  # type: ignore[call-arg]
    assert settings.IDENTITY_DATABASE_URL is not None


SMTP = ("smtp_username", "smtp_password")


def _stage_smtp(target: Path, mode: int = 0o600) -> dict[str, str]:
    """Top-level files as the owner leaves them before ``smtp-install`` (synthetic values)."""
    values = {name: f"SYNTHETIC-{name}-{'x' * 24}" for name in SMTP}
    for name, value in values.items():
        (target / name).write_text(value)
        (target / name).chmod(mode)
    return values


def test_smtp_files_are_optional_for_check(tmp_path: Path) -> None:
    target = _create(tmp_path)
    assert not any((target / "runtime" / name).exists() for name in SMTP)
    assert staging_secrets.check(target, UID) == []


def test_smtp_install_moves_files_with_expected_mode_and_owner(tmp_path: Path) -> None:
    target = _create(tmp_path)
    values = _stage_smtp(target)
    moved = staging_secrets.install_smtp(target, UID)
    assert moved == ["runtime/smtp_username", "runtime/smtp_password"]
    for name in SMTP:
        assert not (target / name).exists()
        installed = target / "runtime" / name
        info = installed.lstat()
        assert stat.S_ISREG(info.st_mode)
        assert stat.S_IMODE(info.st_mode) == 0o400
        assert info.st_uid == UID
        assert installed.read_text() == values[name]
    assert staging_secrets.check(target, UID) == []


def test_smtp_install_is_a_no_op_when_already_installed(tmp_path: Path) -> None:
    target = _create(tmp_path)
    values = _stage_smtp(target)
    staging_secrets.install_smtp(target, UID)
    assert staging_secrets.install_smtp(target, UID) == []
    assert {name: (target / "runtime" / name).read_text() for name in SMTP} == values


def test_smtp_install_refuses_symlinks_and_overwrites(tmp_path: Path) -> None:
    target = _create(tmp_path)
    values = _stage_smtp(target)
    (target / "smtp_password").unlink()
    (target / "smtp_password").symlink_to(target / "database/staging_owner_password")
    with pytest.raises(staging_secrets.ProvisionError, match="smtp_password: not a regular file"):
        staging_secrets.install_smtp(target, UID)
    # Nothing was moved, not even the good file.
    assert (target / "smtp_username").read_text() == values["smtp_username"]
    assert not (target / "runtime/smtp_username").exists()
    (target / "smtp_password").unlink()
    _stage_smtp(target)
    (target / "runtime/smtp_password").write_text("already here")
    (target / "runtime/smtp_password").chmod(0o400)
    with pytest.raises(staging_secrets.ProvisionError, match="exists in both places"):
        staging_secrets.install_smtp(target, UID)
    assert (target / "runtime/smtp_password").read_text() == "already here"


def test_smtp_install_needs_both_files_and_the_runtime_uid(tmp_path: Path) -> None:
    target = _create(tmp_path)
    _stage_smtp(target)
    (target / "smtp_password").unlink()
    with pytest.raises(staging_secrets.ProvisionError, match="smtp_password: missing"):
        staging_secrets.install_smtp(target, UID)
    with pytest.raises(staging_secrets.ProvisionError, match="runtime UID"):
        staging_secrets.install_smtp(target, UID + 1)
    assert (target / "smtp_username").exists()


def test_check_reports_a_bad_or_half_installed_smtp_file(tmp_path: Path) -> None:
    target = _create(tmp_path)
    _stage_smtp(target)
    staging_secrets.install_smtp(target, UID)
    (target / "runtime/smtp_username").chmod(0o440)
    assert staging_secrets.check(target, UID) == ["runtime/smtp_username: mode is not 0400"]
    (target / "runtime/smtp_username").chmod(0o400)
    (target / "runtime/smtp_password").unlink()
    assert staging_secrets.check(target, UID) == [
        "runtime/smtp_password: missing while its pair is installed"
    ]


def test_smtp_install_main_never_prints_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "secrets"
    args = ["--secret-dir", str(target), "--runtime-uid", str(UID)]
    assert staging_secrets.main(["create", *args]) == 0
    values = _stage_smtp(target)
    capsys.readouterr()
    assert staging_secrets.main(["smtp-install", *args]) == 0
    output = capsys.readouterr()
    assert "installed runtime/smtp_username (0400)" in output.out
    assert "installed runtime/smtp_password (0400)" in output.out
    for value in values.values():
        assert value not in output.out + output.err
    assert staging_secrets.main(["smtp-install", *args]) == 0
    assert "already installed" in capsys.readouterr().out
