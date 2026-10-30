"""Static and synthetic checks for the manual staging image publication workflow.

Nothing here contacts a registry or GitHub: the archive-verification shell block
is extracted from the workflow and executed against synthetic OCI layouts.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
PUBLISH = WORKFLOWS / "publish-staging-images.yml"
SHA = "0123456789abcdef0123456789abcdef01234567"
MANIFEST_TYPE = "application/vnd.oci.image.manifest.v1+json"


def _text() -> str:
    return PUBLISH.read_text(encoding="utf-8")


def _run_block(step_name: str) -> str:
    lines = _text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"- name: {step_name}")
    run_idx = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run_idx + 1]) - len(lines[run_idx + 1].lstrip())
    body: list[str] = []
    for line in lines[run_idx + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body) + "\n"


RUN_ID_EXPR = "${{ inputs.ci_run_id || github.event.workflow_run.id }}"


def test_triggers_are_manual_dispatch_and_completed_main_ci() -> None:
    text = _text()
    on_block = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "workflow_dispatch:" in on_block
    run_block = on_block.split("workflow_run:", 1)[1]
    assert "workflows: [ci]" in run_block
    assert "types: [completed]" in run_block
    assert "branches: [main]" in run_block
    for trigger in ("push:", "pull_request", "schedule:", "release:"):
        assert trigger not in on_block


def test_automatic_runs_publish_only_successful_push_runs() -> None:
    job = _text().split("\n  publish:\n", 1)[1].split("    steps:", 1)[0]
    condition = job.split("    if:", 1)[1].split("\n    ", 1)[0]
    assert "github.event_name == 'workflow_dispatch'" in condition
    assert "github.event.workflow_run.event == 'push'" in condition
    assert "github.event.workflow_run.conclusion == 'success'" in condition


def test_run_id_comes_from_the_input_or_the_triggering_run() -> None:
    text = _text()
    assert f"CI_RUN_ID: {RUN_ID_EXPR}" in text
    assert f"run-id: {RUN_ID_EXPR}" in text
    assert "${{ inputs.ci_run_id }}" not in text


def test_permissions_are_scoped_and_environment_gated() -> None:
    text = _text()
    top = text.split("\npermissions:\n", 1)[1].split("\n\n", 1)[0]
    assert top.strip() == "contents: read"
    job = text.split("\n  publish:\n", 1)[1]
    assert "environment: staging-images" in job
    job_perms = job.split("    permissions:\n", 1)[1].split("    env:", 1)[0]
    assert sorted(line.strip() for line in job_perms.splitlines() if line.strip()) == [
        "actions: read",
        "contents: read",
        "packages: write",
    ]
    assert text.count("packages: write") == 1
    assert "persist-credentials: false" in text


def test_ci_workflow_remains_build_only() -> None:
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "packages: write" not in ci
    assert "never publish" in ci
    assert "skopeo" not in ci and "docker push" not in ci


def test_source_run_identity_is_verified() -> None:
    block = _run_block("Verify the source CI run identity")
    for expected in (
        "check .path .github/workflows/ci.yml",
        "check .event push",
        "check .head_branch main",
        "check .status completed",
        "check .conclusion success",
        'check .head_repository.full_name "$GITHUB_REPOSITORY"',
        "^[0-9]+$",
        "^[0-9a-f]{40}$",
    ):
        assert expected in block
    assert 'git merge-base --is-ancestor "$HEAD_SHA" HEAD' in _text()


def test_publishes_exact_archives_by_sha_with_preserved_digest() -> None:
    text = _text()
    assert "name: csa-review-only-${{ steps.run.outputs.head_sha }}" in text
    push = _run_block("Push archives with preserved manifest digests")
    assert "skopeo copy --preserve-digests" in push
    assert '"oci-archive:$app.oci.tar"' in push
    assert '"docker://$REGISTRY_NAMESPACE/csa-$app:$HEAD_SHA"' in push
    assert "--password-stdin" in push
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    for forbidden in ("latest", "buildx", "docker build", "docker push", "--dest-creds"):
        assert forbidden not in code
    verify = _run_block("Re-read registry digests and compare")
    assert 'skopeo inspect --raw "docker://$ref"' in verify
    assert '[ "$actual" = "$expected" ]' in verify


def test_untrusted_inputs_are_not_interpolated_into_scripts() -> None:
    for match in re.finditer(r"run: \|\n((?:[ ]{10,}.*\n|\n)+)", _text()):
        assert "${{" not in match.group(1)


def _blob(layout: Path, payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    (layout / "blobs" / "sha256" / digest).write_bytes(payload)
    return f"sha256:{digest}"


def _archive(
    release: Path,
    app: str,
    *,
    revision: str = SHA,
    manifests: int = 1,
    tamper: bool = False,
) -> None:
    layout = release / f"{app}-src"
    (layout / "blobs" / "sha256").mkdir(parents=True)
    config = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "config": {"Labels": {"org.opencontainers.image.revision": revision}},
        }
    ).encode()
    config_digest = _blob(layout, config)
    manifest = json.dumps(
        {"schemaVersion": 2, "config": {"digest": config_digest}, "layers": []}
    ).encode()
    manifest_digest = _blob(layout, manifest)
    if tamper:
        (layout / "blobs" / "sha256" / manifest_digest.removeprefix("sha256:")).write_bytes(
            manifest + b" "
        )
    entry = {"mediaType": MANIFEST_TYPE, "digest": manifest_digest, "size": len(manifest)}
    (layout / "index.json").write_text(
        json.dumps({"schemaVersion": 2, "manifests": [entry] * manifests})
    )
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    with tarfile.open(release / f"{app}.oci.tar", "w") as tar:
        for path in sorted(layout.rglob("*")):
            tar.add(path, arcname=str(path.relative_to(layout)), recursive=False)
    shutil.rmtree(layout)


def _verify(tmp_path: Path, **frontend: object) -> subprocess.CompletedProcess[str]:
    if shutil.which("jq") is None or shutil.which("sha256sum") is None:
        pytest.skip("jq and sha256sum are required to execute the workflow block")
    release = tmp_path / "release"
    runner = tmp_path / "runner"
    release.mkdir()
    runner.mkdir()
    _archive(release, "python")
    _archive(release, "frontend", **frontend)  # type: ignore[arg-type]
    (release / "release.txt").write_text(
        f"source_revision={SHA}\nplatform=linux/amd64\npromotion=disabled\n"
    )
    sums = subprocess.run(
        ["sha256sum", "python.oci.tar", "frontend.oci.tar", "release.txt"],
        cwd=release,
        check=True,
        capture_output=True,
        text=True,
    )
    (release / "SHA256SUMS").write_text(sums.stdout)
    output = runner / "output"
    output.touch()
    return subprocess.run(
        ["bash", "-c", _run_block("Verify archive checksums, revision and single-manifest layout")],
        cwd=release,
        env={
            "PATH": "/usr/bin:/bin",
            "HEAD_SHA": SHA,
            "RUNNER_TEMP": str(runner),
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_archive_verification_accepts_matching_synthetic_layouts(tmp_path: Path) -> None:
    result = _verify(tmp_path)
    assert result.returncode == 0, result.stderr
    outputs = (tmp_path / "runner" / "output").read_text().splitlines()
    assert [line.split("=", 1)[0] for line in outputs] == ["python_digest", "frontend_digest"]
    assert all(re.fullmatch(r"\w+=sha256:[0-9a-f]{64}", line) for line in outputs)


@pytest.mark.parametrize(
    "frontend",
    [{"revision": "f" * 40}, {"manifests": 2}, {"tamper": True}],
    ids=["wrong-revision", "multiple-manifests", "tampered-manifest"],
)
def test_archive_verification_rejects_mismatches(
    tmp_path: Path, frontend: dict[str, object]
) -> None:
    result = _verify(tmp_path, **frontend)
    assert result.returncode != 0
    assert "frontend_digest" not in (tmp_path / "runner" / "output").read_text()
