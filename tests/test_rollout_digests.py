"""A rollout given image digests as arguments: no GitHub lookup, no staged gate."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn

import pytest
from scripts import staging_rollout as sr
from scripts.rollout_targets import Target
from scripts.staging_rollout import RolloutError

from tests.test_rollout_gate import FakeDeployments
from tests.test_rollout_production import HOST_TEXT, HostedRig
from tests.test_staging_rollout import DIGEST_A, DIGEST_B, SHA, FakeRunner, ok

DIGESTS = {"python": DIGEST_A, "frontend": DIGEST_B}
OTHER_DIGEST = "sha256:" + "c" * 64
REFUSAL = f"commit {SHA[:7]} has not been rolled out to staging"


def _no_gh(argv: Sequence[str], env: Mapping[str, str] | None) -> NoReturn:
    raise AssertionError(f"unexpected gh call: {' '.join(argv)}")


class DigestRig:
    """A production-like gated target on the hosted rig, with gh refusing every call."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.rig = HostedRig(tmp_path, monkeypatch, HOST_TEXT)
        self.target: Target = dataclasses.replace(self.rig.target, staged_gate=True)
        self.rig.runner.rules.insert(0, (lambda argv: argv[0] == "gh", _no_gh))

    @property
    def runner(self) -> FakeRunner:
        return self.rig.runner

    def plan(self, digests: Mapping[str, str] | None = DIGESTS) -> sr.Plan:
        return sr.build_plan(
            self.runner,
            self.rig.repo,
            docker_config=self.rig.docker_config,
            secret_dir=self.rig.secret_dir,
            checks_dir=self.rig.checks,
            target=self.target,
            image_digests=digests,
        )

    def apply(self, digests: Mapping[str, str] | None = DIGESTS) -> int:
        host_digest = sr.host_settings_digest(self.plan(digests).host_values)
        self.runner.calls.clear()
        return sr.apply(
            self.runner,
            self.rig.repo,
            ["0486"],
            sha=SHA,
            docker_config=self.rig.docker_config,
            secret_dir=self.rig.secret_dir,
            checks_dir=self.rig.checks,
            sleep=lambda _: None,
            out=self.rig.lines.append,
            target=self.target,
            host_digest=host_digest,
            image_digests=digests,
        )


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DigestRig:
    return DigestRig(tmp_path, monkeypatch)


def test_images_from_digests_names_each_app_by_digest(rig: DigestRig) -> None:
    assert sr.images_from_digests(DIGESTS, rig.target) == {
        "python": f"{rig.target.image_owner}/csa-python@{DIGEST_A}",
        "frontend": f"{rig.target.image_owner}/csa-frontend@{DIGEST_B}",
    }


def test_digests_skip_every_gh_call_and_the_gate_on_production(rig: DigestRig) -> None:
    plan = rig.plan()
    assert plan.images == sr.images_from_digests(DIGESTS, rig.target)
    assert plan.staged_on == ""
    assert all(argv[0] != "gh" for argv in rig.runner.argvs)
    assert not any("versions" in " ".join(argv) for argv in rig.runner.argvs)


def test_digests_apply_runs_a_whole_rollout_without_gh(rig: DigestRig) -> None:
    assert rig.apply() == 0
    assert rig.rig.lines[-1] == f"Rollout of {SHA[:7]} finished."
    assert all(argv[0] != "gh" for argv in rig.runner.argvs)
    pulled = [argv[-1] for argv in rig.runner.argvs if argv[3:4] == ["pull"]]
    assert pulled == list(sr.images_from_digests(DIGESTS, rig.target).values())


def test_without_digests_production_still_looks_up_and_refuses_an_unstaged_commit(
    rig: DigestRig,
) -> None:
    rig.runner.rules[:1] = [*FakeDeployments().rules()]
    with pytest.raises(RolloutError) as caught:
        rig.plan(None)
    assert caught.value.step == "gate"
    assert caught.value.message == REFUSAL
    gh_calls = [argv for argv in rig.runner.argvs if argv[0] == "gh"]
    assert gh_calls
    assert not any("versions" in " ".join(argv) for argv in gh_calls)


@pytest.mark.parametrize(
    "bad", [DIGEST_A + "\n", "sha256:" + "A" * 64, "sha256:" + "a" * 63, DIGEST_A[7:], ""]
)
def test_a_malformed_digest_is_refused_with_a_newline_and_with_uppercase(
    rig: DigestRig, bad: str
) -> None:
    for app in ("python", "frontend"):
        with pytest.raises(RolloutError) as caught:
            sr.images_from_digests({**DIGESTS, app: bad}, rig.target)
        assert caught.value.step == "plan"
        assert f"csa-{app}" in caught.value.message
    with pytest.raises(RolloutError):
        rig.plan({**DIGESTS, "python": bad})
    # Only the checkout is read: no docker command runs on a refused digest.
    assert all(argv[0] == "git" for argv in rig.runner.argvs)


def test_digests_must_name_exactly_the_two_apps(rig: DigestRig) -> None:
    for wrong in ({"python": DIGEST_A}, {**DIGESTS, "extra": DIGEST_A}):
        with pytest.raises(RolloutError):
            sr.images_from_digests(wrong, rig.target)


@pytest.mark.parametrize("bad", [DIGEST_A + "\n", "sha256:" + "A" * 64, "sha256:short", ""])
def test_the_parser_refuses_a_malformed_digest(bad: str) -> None:
    for command in ("plan", "apply"):
        argv = [command, "--python-digest", bad, "--frontend-digest", DIGEST_B]
        if command == "apply":
            argv += ["--sha", SHA]
        with pytest.raises(SystemExit):
            sr._parser().parse_args(argv)


def test_the_parser_takes_both_digests_on_plan_and_apply() -> None:
    both = ["--python-digest", DIGEST_A, "--frontend-digest", DIGEST_B]
    for argv in (["plan", *both], ["apply", "--sha", SHA, *both]):
        args = sr._parser().parse_args(argv)
        assert (args.python_digest, args.frontend_digest) == (DIGEST_A, DIGEST_B)
    assert sr._parser().parse_args(["plan"]).python_digest is None


@pytest.mark.parametrize("only", ["--python-digest", "--frontend-digest"])
@pytest.mark.parametrize("command", ["plan", "apply"])
def test_only_one_digest_option_is_a_parser_error(
    only: str, command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--target", "production", command, only, DIGEST_A]
    if command == "apply":
        argv += ["--sha", SHA, "--host-digest", "0" * 12]
    with pytest.raises(SystemExit) as caught:
        sr.main(argv, runner=sr.SubprocessRunner(cwd=sr.REPO_ROOT))
    assert caught.value.code == 2
    assert "--python-digest and --frontend-digest" in capsys.readouterr().err


def test_a_digest_the_pull_cannot_verify_stops_before_any_recreate(rig: DigestRig) -> None:
    """The registry answers with another digest than the one the workflow resolved."""
    python_ref = sr.images_from_digests(DIGESTS, rig.target)["python"]
    wrong = {
        "RepoDigests": [python_ref.split("@")[0] + "@" + OTHER_DIGEST],
        "Config": {"Labels": {"org.opencontainers.image.revision": SHA}},
    }
    rig.runner.rules.insert(
        0,
        (
            lambda argv: argv[3:5] == ["image", "inspect"] and argv[-1] == python_ref,
            ok(json.dumps([wrong])),
        ),
    )
    assert rig.apply() == 1
    assert "Stopped at pull:" in "\n".join(rig.rig.lines)
    assert not any("up" in argv or "stop" in argv for argv in rig.runner.argvs)
    assert not any(argv[3:4] == ["exec"] for argv in rig.runner.argvs)


def test_the_printed_apply_command_carries_the_digests(rig: DigestRig) -> None:
    lines = sr.format_plan(rig.plan(), target=rig.target).splitlines()
    host_digest = sr.host_settings_digest(rig.plan().host_values)
    assert not any(line.startswith("Staged on:") for line in lines)
    assert lines[-1] == (
        f"{sr.MODULE_COMMAND} --target production apply --sha {SHA} --migrations 0486"
        f" --host-digest {host_digest} --python-digest {DIGEST_A} --frontend-digest {DIGEST_B}"
    )


def test_a_plan_without_digests_prints_no_digest_options(rig: DigestRig) -> None:
    rig.runner.rules[:1] = [
        *FakeDeployments(
            {
                "id": 1,
                "sha": SHA,
                "environment": "staging",
                "created_at": "2026-11-09T10:00:00Z",
                "statuses": [{"id": 10, "state": "success", "created_at": "2026-11-09T10:00:00Z"}],
            }
        ).rules()
    ]
    lines = sr.format_plan(rig.plan(None), target=rig.target).splitlines()
    assert "Staged on: 2026-11-09T10:00:00Z" in lines
    assert "digest" not in lines[-1].replace("--host-digest", "")
