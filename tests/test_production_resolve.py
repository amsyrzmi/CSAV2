"""deploy/production/resolve_commit.sh decides and checks the commit production deploys.

The script runs against stub `gh` and `curl` executables on PATH. The stubs log their arguments
and answer from fixture files, so every API answer (and every failure) is under test control.
Because a host given image digests skips its own staged gate, this script is that gate: each
test that fails a check also asserts that stdout stayed empty and that nothing later ran.
"""

from __future__ import annotations

import json
import re
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "production" / "resolve_commit.sh"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

REPOSITORY = "example-owner/example-repo"
IMAGE_NAMESPACE = "ghcr.io/example-owner"
GH_TOKEN = "ghs_FAKEGITHUBTOKEN123"
REGISTRY_TOKEN = "regtoken-FAKE-456"
TIP = "0123456789abcdef0123456789abcdef01234567"
OLDER = "89abcdef0123456789abcdef0123456789abcdef"
SHA7 = TIP[:7]
PY_DIGEST = "sha256:" + "0123456789abcdef" * 4
FE_DIGEST = "sha256:" + "fedcba9876543210" * 4
STAGED_ON = "2026-11-13T08:30:00Z"
ACCEPT_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)

# `gh api <path>` answers from $STUB_DIR; a fail-gh-<key> file makes that call exit 1.
GH_STUB = r"""#!/bin/bash
IFS=$'\t'
printf 'gh\t%s\n' "$*" >> "$STUB_LOG"
IFS=' '
[[ "$1" == api && $# -eq 2 ]] || exit 2
case "$2" in
    */commits/main) key=commits ;;
    */compare/*) key=compare ;;
    */actions/workflows/ci.yml/runs\?head_sha=*\&branch=main\&event=push) key=runs ;;
    */deployments\?environment=staging\&sha=*\&per_page=100) key=deployments ;;
    */deployments/*/statuses\?per_page=100)
        id="${2%/statuses*}"
        key="statuses-${id##*/}"
        ;;
    *) exit 2 ;;
esac
if [[ -e "$STUB_DIR/fail-gh-$key" ]]; then
    echo "gh: stub failure for $key (token $GH_TOKEN)" >&2
    exit 1
fi
cat "$STUB_DIR/$key.json"
"""

# curl is called as `curl ... -K - ... <url>` with the credentials on stdin. The stub logs argv
# and stdin to separate files so a test can prove the token never reached a command line.
CURL_STUB = r"""#!/bin/bash
IFS=$'\t'
printf 'curl\t%s\n' "$*" >> "$STUB_LOG"
IFS=' '
stdin="$(cat)"
headers=""
url=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -D) headers="$2"; shift ;;
        -o|-w|-H) shift ;;
        -*) ;;
        *) url="$1" ;;
    esac
    shift
done
printf '%s\t%s\n' "$url" "${stdin//$'\n'/ }" >> "$STUB_STDIN_LOG"
case "$url" in
    https://ghcr.io/token\?*)
        app="${url##*/csa-}"
        app="${app%:pull}"
        [[ -e "$STUB_DIR/fail-curl-token-$app" ]] && exit 22
        cat "$STUB_DIR/token-$app.json"
        ;;
    https://ghcr.io/v2/*/manifests/*)
        app="${url#*/csa-}"
        app="${app%%/*}"
        [[ -e "$STUB_DIR/fail-curl-head-$app" ]] && exit 6
        cat "$STUB_DIR/head-$app.txt" > "$headers"
        printf '%s' "$(cat "$STUB_DIR/code-$app" 2> /dev/null || echo 200)"
        ;;
    *) exit 2 ;;
esac
"""


def _statuses(*states: tuple[str, str]) -> str:
    return json.dumps([{"state": state, "created_at": at} for state, at in states])


def _headers(digest: str, name: str = "Docker-Content-Digest") -> str:
    kind = "application/vnd.oci.image.manifest.v1+json"
    return f"HTTP/2 200\r\ncontent-type: {kind}\r\n{name}: {digest}\r\n\r\n"


def _run(
    sha: str = TIP, conclusion: str | None = "success", status: str = "completed"
) -> dict[str, object]:
    return {
        "head_sha": sha,
        "head_branch": "main",
        "event": "push",
        "status": status,
        "conclusion": conclusion,
    }


def _defaults() -> dict[str, str]:
    return {
        "commits.json": json.dumps({"sha": TIP}),
        "compare.json": json.dumps({"status": "ahead"}),
        "runs.json": json.dumps({"workflow_runs": [_run()]}),
        "deployments.json": json.dumps(
            [
                {"id": 11, "sha": TIP, "environment": "staging"},
                {"id": 12, "sha": TIP, "environment": "staging"},
                {"id": 10, "sha": OLDER, "environment": "staging"},
            ]
        ),
        "statuses-11.json": _statuses(
            ("pending", "2026-11-13T09:00:00Z"), ("success", "2026-11-13T10:00:00Z")
        ),
        "statuses-12.json": _statuses(("failure", "2026-11-13T07:00:00Z"), ("success", STAGED_ON)),
        "statuses-10.json": _statuses(("success", "2026-11-12T08:00:00Z")),
        "token-python.json": json.dumps({"token": REGISTRY_TOKEN}),
        "token-frontend.json": json.dumps({"token": REGISTRY_TOKEN}),
        "head-python.txt": _headers(PY_DIGEST),
        "head-frontend.txt": _headers(FE_DIGEST),
    }


class Harness:
    """Stub executables on PATH, their fixtures and a runner for the script."""

    def __init__(self, tmp_path: Path) -> None:
        """Write the stubs and the default (fully passing) fixtures under tmp_path."""
        self.dir = tmp_path / "stub"
        self.dir.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.log = tmp_path / "calls.log"
        self.stdin_log = tmp_path / "stdin.log"
        for name, body in (("gh", GH_STUB), ("curl", CURL_STUB)):
            path = self.bin / name
            path.write_text(body)
            path.chmod(0o755)
        for name, content in _defaults().items():
            self.set(name, content)

    def set(self, name: str, content: str) -> None:
        """Write a fixture file."""
        (self.dir / name).write_text(content)

    def flag(self, name: str) -> None:
        """Create an empty marker file (the stubs fail when one exists)."""
        (self.dir / name).write_text("")

    def calls(self) -> list[list[str]]:
        """The stub calls so far, as [tool, argv] pairs."""
        if not self.log.exists():
            return []
        calls = []
        for line in self.log.read_text().splitlines():
            tool, *argv = line.split("\t")
            calls.append([tool, " ".join(argv)])
        return calls

    def run(
        self,
        *args: str,
        env_extra: dict[str, str] | None = None,
        token: str | None = GH_TOKEN,
        namespace: str | None = IMAGE_NAMESPACE,
    ) -> subprocess.CompletedProcess[str]:
        """Run the script with the stubs first on PATH."""
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "REPOSITORY": REPOSITORY,
            "STUB_DIR": str(self.dir),
            "STUB_LOG": str(self.log),
            "STUB_STDIN_LOG": str(self.stdin_log),
            **(env_extra or {}),
        }
        if token is not None:
            env["GH_TOKEN"] = token
        if namespace is not None:
            env["IMAGE_NAMESPACE"] = namespace
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def _assert_refused(result: subprocess.CompletedProcess[str], message: str | None = None) -> None:
    assert result.returncode != 0, result.stdout
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1, result.stderr
    if message is not None:
        assert result.stderr == message + "\n"
    assert GH_TOKEN not in result.stderr
    assert REGISTRY_TOKEN not in result.stderr


def test_the_script_is_executable_and_parses() -> None:
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    tracked = subprocess.run(
        ["git", "ls-files", "-s", str(SCRIPT)], capture_output=True, text=True, cwd=ROOT, check=True
    ).stdout
    assert tracked == "" or tracked.startswith("100755")
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_the_ci_workflow_file_is_the_one_the_script_asks_about() -> None:
    assert CI_WORKFLOW.is_file()
    assert re.search(r"^name: ci$", CI_WORKFLOW.read_text(), re.MULTILINE)
    assert "workflows/ci.yml/runs" in SCRIPT.read_text()


def test_default_resolves_the_tip_and_prints_all_five_values(harness: Harness) -> None:
    result = harness.run()
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        f"sha={TIP}\npython_digest={PY_DIGEST}\nfrontend_digest={FE_DIGEST}\nstaged_on={STAGED_ON}\nexplicit=false\n"
    )
    assert result.stderr == ""
    gh_calls = [call[1] for call in harness.calls() if call[0] == "gh"]
    assert gh_calls[:4] == [
        f"api repos/{REPOSITORY}/commits/main",
        f"api repos/{REPOSITORY}/compare/{TIP}...main",
        f"api repos/{REPOSITORY}/actions/workflows/ci.yml/runs"
        f"?head_sha={TIP}&branch=main&event=push",
        f"api repos/{REPOSITORY}/deployments?environment=staging&sha={TIP}&per_page=100",
    ]
    # Only the tip's own deployments are read, never an older commit's.
    assert f"api repos/{REPOSITORY}/deployments/11/statuses?per_page=100" in gh_calls
    assert f"api repos/{REPOSITORY}/deployments/12/statuses?per_page=100" in gh_calls
    assert not any("/deployments/10/" in call for call in gh_calls)


def test_explicit_sha_resolves_without_reading_the_tip(harness: Harness) -> None:
    result = harness.run(TIP)
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("explicit=true\n")
    assert not any(call[1].endswith("/commits/main") for call in harness.calls() if call[0] == "gh")


def test_an_empty_input_means_the_tip(harness: Harness) -> None:
    result = harness.run("")
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("explicit=false\n")


def test_registry_calls_use_the_namespace_owner_the_accept_types_and_keep_the_token_off_argv(
    harness: Harness,
) -> None:
    result = harness.run()
    assert result.returncode == 0, result.stderr
    curls = [call[1] for call in harness.calls() if call[0] == "curl"]
    assert len(curls) == 4
    for app in ("python", "frontend"):
        token_call = next(c for c in curls if "ghcr.io/token" in c and f"/csa-{app}:pull" in c)
        assert (
            f"https://ghcr.io/token?service=ghcr.io&scope=repository:example-owner/csa-{app}:pull"
            in token_call
        )
        head_call = next(c for c in curls if "/manifests/" in c and f"/csa-{app}/" in c)
        assert f"https://ghcr.io/v2/example-owner/csa-{app}/manifests/{TIP}" in head_call
        assert "--head" in head_call.split()
        for media_type in ACCEPT_TYPES:
            assert media_type in head_call
    argv_log = harness.log.read_text()
    assert GH_TOKEN not in argv_log
    assert REGISTRY_TOKEN not in argv_log
    # GH_TOKEN is only for gh: curl never gets it, on argv or on stdin. The token request is
    # anonymous (nothing on stdin, no credentials option); the HEAD carries the registry token.
    stdin_lines = [line.split("\t") for line in harness.stdin_log.read_text().splitlines()]
    assert len(stdin_lines) == 4
    assert GH_TOKEN not in harness.stdin_log.read_text()
    for url, stdin in stdin_lines:
        if "/token?" in url:
            assert stdin == ""
        else:
            assert f"Bearer {REGISTRY_TOKEN}" in stdin
    for command in curls:
        assert "--max-time 30" in command
        if "ghcr.io/token" in command:
            assert "-K" not in command.split()
            assert "-u" not in command.split()
            assert "--user" not in command.split()
    assert GH_TOKEN not in result.stdout + result.stderr
    assert REGISTRY_TOKEN not in result.stdout + result.stderr


def test_the_digest_header_is_read_case_insensitively_and_the_carriage_return_stripped(
    harness: Harness,
) -> None:
    harness.set("head-python.txt", _headers(PY_DIGEST, name="docker-content-digest"))
    harness.set("head-frontend.txt", _headers(FE_DIGEST, name="DOCKER-CONTENT-DIGEST"))
    result = harness.run()
    assert result.returncode == 0, result.stderr
    assert f"python_digest={PY_DIGEST}\n" in result.stdout
    assert f"frontend_digest={FE_DIGEST}\n" in result.stdout


def test_staged_on_is_the_earliest_success_across_deployments(harness: Harness) -> None:
    harness.set("statuses-11.json", _statuses(("success", "2026-11-13T06:00:00Z")))
    harness.set("statuses-12.json", _statuses(("success", "2026-11-13T07:00:00Z")))
    result = harness.run()
    assert result.returncode == 0, result.stderr
    assert "staged_on=2026-11-13T06:00:00Z\n" in result.stdout


def test_default_with_an_unstaged_tip_does_not_fall_back_to_an_older_staged_commit(
    harness: Harness,
) -> None:
    # The older commit is staged (and even listed by the stub); the tip has no deployment.
    harness.set(
        "deployments.json",
        json.dumps([{"id": 10, "sha": OLDER, "environment": "staging"}]),
    )
    result = harness.run()
    _assert_refused(result, f"main's tip {SHA7} is not staged yet; wait for deploy-staging")
    calls = harness.calls()
    assert not any("/deployments/10/" in call[1] for call in calls)
    assert not any(call[0] == "curl" for call in calls)


def test_explicit_sha_message_differs(harness: Harness) -> None:
    harness.set("deployments.json", "[]")
    _assert_refused(harness.run(TIP), f"commit {SHA7} has not been rolled out to staging")
    harness.set("deployments.json", "[]")
    _assert_refused(harness.run(), f"main's tip {SHA7} is not staged yet; wait for deploy-staging")


def test_a_deployment_without_a_success_status_is_not_staged(harness: Harness) -> None:
    harness.set("deployments.json", json.dumps([{"id": 11, "sha": TIP, "environment": "staging"}]))
    harness.set(
        "statuses-11.json",
        _statuses(("pending", "2026-11-13T09:00:00Z"), ("failure", "2026-11-13T10:00:00Z")),
    )
    _assert_refused(harness.run(), f"main's tip {SHA7} is not staged yet; wait for deploy-staging")


FAILED_CHECKS = {
    "not-on-main-diverged": (
        {"compare.json": json.dumps({"status": "diverged"})},
        f"commit {SHA7} is not on main",
    ),
    "not-on-main-behind": (
        {"compare.json": json.dumps({"status": "behind"})},
        f"commit {SHA7} is not on main",
    ),
    "ci-failed": (
        {"runs.json": json.dumps({"workflow_runs": [_run(conclusion="failure")]})},
        f"the ci run for {SHA7} has not succeeded on main",
    ),
    "ci-in-progress": (
        {"runs.json": json.dumps({"workflow_runs": [_run(conclusion=None, status="in_progress")]})},
        f"the ci run for {SHA7} has not succeeded on main",
    ),
    "ci-no-runs": (
        {"runs.json": json.dumps({"total_count": 0, "workflow_runs": []})},
        f"the ci run for {SHA7} has not succeeded on main",
    ),
    "ci-run-for-another-sha": (
        {"runs.json": json.dumps({"workflow_runs": [_run(sha=OLDER)]})},
        f"the ci run for {SHA7} has not succeeded on main",
    ),
    "not-staged": (
        {"deployments.json": "[]"},
        f"main's tip {SHA7} is not staged yet; wait for deploy-staging",
    ),
    "python-image-missing": (
        {"code-python": "404"},
        f"images for {SHA7} are not published (csa-python)",
    ),
    "frontend-image-missing": (
        {"code-frontend": "404"},
        f"images for {SHA7} are not published (csa-frontend)",
    ),
    "python-digest-malformed": (
        {"head-python.txt": _headers("sha256:" + "0123456789ABCDEF" * 4)},
        "the registry returned a malformed digest for csa-python",
    ),
    "frontend-digest-malformed": (
        {"head-frontend.txt": _headers("sha256:abc")},
        "the registry returned a malformed digest for csa-frontend",
    ),
}


@pytest.mark.parametrize("name", FAILED_CHECKS)
def test_each_check_stops_with_its_exact_message_and_prints_nothing_to_stdout(
    harness: Harness, name: str
) -> None:
    overrides, message = FAILED_CHECKS[name]
    for file, content in overrides.items():
        harness.set(file, content)
    _assert_refused(harness.run(), message)


def test_a_failed_check_stops_before_the_later_calls(harness: Harness) -> None:
    harness.set("compare.json", json.dumps({"status": "diverged"}))
    _assert_refused(harness.run(), f"commit {SHA7} is not on main")
    calls = [call[1] for call in harness.calls()]
    assert not any("actions/workflows" in call or "deployments" in call for call in calls)
    assert not any(call.startswith("-") or "ghcr.io" in call for call in calls)


def test_a_ci_run_that_failed_then_a_later_success_for_the_same_sha_passes(
    harness: Harness,
) -> None:
    harness.set(
        "runs.json",
        json.dumps({"workflow_runs": [_run(conclusion="success"), _run(conclusion="failure")]}),
    )
    assert harness.run().returncode == 0
    harness.set(
        "runs.json",
        json.dumps({"workflow_runs": [_run(conclusion="failure"), _run(conclusion="success")]}),
    )
    assert harness.run().returncode == 0


BAD_SHAS = [
    pytest.param("ABCDEF0123456789abcdef0123456789abcdef01", id="uppercase"),
    pytest.param(TIP[:39], id="39-chars"),
    pytest.param(TIP + "0", id="41-chars"),
    pytest.param("main", id="a-ref"),
    pytest.param(TIP[:7], id="abbreviated"),
    pytest.param(TIP + "\n", id="trailing-newline"),
    pytest.param(TIP[:39] + "\n" + TIP[:39] + "\n", id="embedded-newline"),
    pytest.param(TIP[:39] + "٣", id="arabic-indic-digit"),
    pytest.param("0123456789abcdef0123456789abcdef0123456g", id="non-hex"),
    pytest.param(f"{TIP} extra", id="trailing-text"),
]


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("bad", BAD_SHAS)
def test_a_malformed_input_sha_is_refused_before_anything_is_called(
    harness: Harness, bad: str, locale: str
) -> None:
    result = harness.run(bad, env_extra={"LC_ALL": locale, "LANG": locale})
    _assert_refused(result, "sha must be the full 40 character lowercase commit SHA")
    assert harness.calls() == []


def test_a_registry_digest_must_be_one_well_formed_sha256(harness: Harness) -> None:
    bad_digests = [
        "",
        "sha256:",
        "sha256:" + "0" * 63,
        "sha256:" + "0" * 65,
        "sha256:" + "g" * 64,
        "sha512:" + "0" * 64,
        "SHA256:" + "0" * 64,
        PY_DIGEST + " trailing",
    ]
    for digest in bad_digests:
        harness.set("head-python.txt", _headers(digest))
        _assert_refused(harness.run(), "the registry returned a malformed digest for csa-python")


def test_a_missing_or_repeated_digest_header_is_refused(harness: Harness) -> None:
    harness.set("head-python.txt", "HTTP/2 200\r\ncontent-type: text/plain\r\n\r\n")
    _assert_refused(harness.run())
    harness.set(
        "head-python.txt",
        _headers(PY_DIGEST) + "Docker-Content-Digest: " + PY_DIGEST + "\r\n",
    )
    _assert_refused(harness.run())


def test_an_unexpected_registry_status_is_refused(harness: Harness) -> None:
    for code in ("401", "403", "500", "302", ""):
        harness.set("code-python", code)
        _assert_refused(harness.run())


GH_PATHS = {
    "commits": "/commits/main",
    "compare": "/compare/",
    "runs": "/actions/workflows/ci.yml/runs",
    "deployments": "/deployments?environment=staging",
    "statuses-11": "/deployments/11/statuses",
    "statuses-12": "/deployments/12/statuses",
}
GH_KEYS = list(GH_PATHS)


@pytest.mark.parametrize("key", GH_KEYS)
def test_a_failing_gh_call_fails_closed(harness: Harness, key: str) -> None:
    harness.flag(f"fail-gh-{key}")
    _assert_refused(harness.run())
    assert any(GH_PATHS[key] in call[1] for call in harness.calls() if call[0] == "gh")
    assert GH_TOKEN not in harness.log.read_text()


@pytest.mark.parametrize("name", ["token-python", "head-python", "token-frontend", "head-frontend"])
def test_a_failing_curl_call_fails_closed(harness: Harness, name: str) -> None:
    harness.flag(f"fail-curl-{name}")
    _assert_refused(harness.run())
    assert any(call[0] == "curl" for call in harness.calls())


GARBAGE = ["", "not json", "{", "null", '"a string"', "[]", "{}"]


@pytest.mark.parametrize("key", ["commits", "compare", "runs", "deployments", "statuses-11"])
@pytest.mark.parametrize("garbage", GARBAGE)
def test_malformed_or_empty_gh_json_fails_closed(harness: Harness, key: str, garbage: str) -> None:
    # One deployment, so an empty status list (a legitimate answer) means "not staged".
    harness.set("deployments.json", json.dumps([{"id": 11, "sha": TIP, "environment": "staging"}]))
    harness.set(f"{key}.json", garbage)
    _assert_refused(harness.run())
    assert any(GH_PATHS[key] in call[1] for call in harness.calls() if call[0] == "gh")


@pytest.mark.parametrize("app", ["python", "frontend"])
@pytest.mark.parametrize(
    "garbage", ["", "not json", "{}", '{"token": null}', '{"token": ""}', "[]"]
)
def test_a_token_answer_without_a_usable_token_fails_closed(
    harness: Harness, app: str, garbage: str
) -> None:
    harness.set(f"token-{app}.json", garbage)
    _assert_refused(harness.run())
    assert not any(
        "/manifests/" in call[1] and f"/csa-{app}/" in call[1] for call in harness.calls()
    )


def test_a_deployment_id_that_is_not_a_number_is_refused(harness: Harness) -> None:
    harness.set(
        "deployments.json",
        json.dumps([{"id": "11; echo pwned", "sha": TIP, "environment": "staging"}]),
    )
    _assert_refused(harness.run())


@pytest.mark.parametrize(
    "created_at",
    [
        "2026-11-13",
        "2026-11-13T10:00:00Z\nexplicit=true",
        "yesterday",
        "",
        "2026-11-13T10:00:00+08:00",
    ],
)
def test_staged_on_must_be_a_plain_utc_timestamp(harness: Harness, created_at: str) -> None:
    harness.set("deployments.json", json.dumps([{"id": 11, "sha": TIP, "environment": "staging"}]))
    harness.set("statuses-11.json", _statuses(("success", created_at)))
    _assert_refused(harness.run())


def test_a_tip_that_is_not_a_full_sha_is_refused(harness: Harness) -> None:
    harness.set("commits.json", json.dumps({"sha": "main\nexplicit=true"}))
    _assert_refused(harness.run())


@pytest.mark.parametrize("token", [None, ""])
def test_a_missing_or_empty_gh_token_is_refused_before_any_call(
    harness: Harness, token: str | None
) -> None:
    result = harness.run(token=token)
    assert result.returncode != 0
    assert result.stdout == ""
    assert harness.calls() == []


@pytest.mark.parametrize("repository", ["", "no-slash", "a/b/c", "owner/na me", "owner/\nname"])
def test_a_malformed_repository_is_refused_before_any_call(
    harness: Harness, repository: str
) -> None:
    result = harness.run(env_extra={"REPOSITORY": repository})
    assert result.returncode != 0
    assert result.stdout == ""
    assert harness.calls() == []


@pytest.mark.parametrize(
    "namespace",
    [
        None,
        "",
        "ghcr.io/Example-Owner",
        "ghcr.io/example-owner/",
        "https://ghcr.io/example-owner",
        "docker.io/example-owner",
    ],
)
def test_a_missing_or_malformed_namespace_is_refused_before_any_call(
    harness: Harness, namespace: str | None
) -> None:
    result = harness.run(namespace=namespace)
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "IMAGE_NAMESPACE must be set to ghcr.io/<lowercase owner>\n"
    assert harness.calls() == []


def test_the_registry_name_comes_from_the_namespace_not_the_repository(harness: Harness) -> None:
    result = harness.run(namespace="ghcr.io/other-org")
    assert result.returncode == 0, result.stderr
    curls = " ".join(call[1] for call in harness.calls() if call[0] == "curl")
    assert "repository:other-org/csa-python:pull" in curls
    assert "/v2/other-org/csa-frontend/manifests/" in curls
    assert "example-owner/csa-" not in curls


def test_more_than_one_argument_is_refused(harness: Harness) -> None:
    result = harness.run(TIP, TIP)
    assert result.returncode != 0
    assert result.stdout == ""
    assert harness.calls() == []


# ---------------------------------------------------------------------------------------------
# public_smoke.sh: the last step of a production deploy asks the console origin for `/`.

SMOKE_SCRIPT = ROOT / "deploy" / "production" / "public_smoke.sh"
ORIGIN = "https://console.example.test"

# curl is called as `curl ... -K - ... ` with the url on stdin (a curl config), so the origin
# never shows in `ps`. The stub logs argv and the config to separate files, and answers with the
# fixture status code; a fail-curl file makes it exit like a transport failure.
SMOKE_CURL_STUB = r"""#!/bin/bash
IFS=$'\t'
printf 'curl\t%s\n' "$*" >> "$STUB_LOG"
IFS=' '
stdin="$(cat)"
printf '%s\n' "${stdin//$'\n'/ }" >> "$STUB_STDIN_LOG"
[[ -e "$STUB_DIR/fail-curl" ]] && exit 28
printf '%s' "$(cat "$STUB_DIR/smoke-code" 2> /dev/null || echo 200)"
"""


class SmokeHarness:
    """A stub curl on PATH and a runner for public_smoke.sh."""

    def __init__(self, tmp_path: Path) -> None:
        """Write the stub curl and a passing default answer under tmp_path."""
        self.dir = tmp_path / "smoke-stub"
        self.dir.mkdir()
        self.bin = tmp_path / "smoke-bin"
        self.bin.mkdir()
        self.log = tmp_path / "smoke-calls.log"
        self.stdin_log = tmp_path / "smoke-stdin.log"
        path = self.bin / "curl"
        path.write_text(SMOKE_CURL_STUB)
        path.chmod(0o755)
        self.code("200")

    def code(self, status: str) -> None:
        """Set the HTTP status the stub curl prints."""
        (self.dir / "smoke-code").write_text(status)

    def flag(self, name: str) -> None:
        """Create an empty marker file (the stub fails when one exists)."""
        (self.dir / name).write_text("")

    def calls(self) -> list[str]:
        """The curl argv lines logged so far."""
        if not self.log.exists():
            return []
        return [line.removeprefix("curl\t") for line in self.log.read_text().splitlines()]

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run the script with the stub curl first on PATH."""
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "STUB_DIR": str(self.dir),
            "STUB_LOG": str(self.log),
            "STUB_STDIN_LOG": str(self.stdin_log),
        }
        return subprocess.run(
            ["bash", str(SMOKE_SCRIPT), *args],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )


@pytest.fixture
def smoke(tmp_path: Path) -> SmokeHarness:
    return SmokeHarness(tmp_path)


def _assert_never_prints(result: subprocess.CompletedProcess[str], secret: str) -> None:
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert "console.example" not in result.stdout + result.stderr


def test_the_smoke_script_is_executable_and_parses() -> None:
    assert SMOKE_SCRIPT.stat().st_mode & stat.S_IXUSR
    tracked = subprocess.run(
        ["git", "ls-files", "-s", str(SMOKE_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    ).stdout
    assert tracked == "" or tracked.startswith("100755")
    subprocess.run(["bash", "-n", str(SMOKE_SCRIPT)], check=True)


@pytest.mark.parametrize("status", ["200", "204", "301", "302", "303", "307", "308", "399"])
def test_public_smoke_passes_on_200_and_on_a_login_redirect(
    smoke: SmokeHarness, status: str
) -> None:
    smoke.code(status)
    result = smoke.run(ORIGIN)
    assert result.returncode == 0, result.stderr
    _assert_never_prints(result, ORIGIN)
    (call,) = smoke.calls()
    argv = call.split()
    # Bounded and retried; only the status is read; a redirect is not followed.
    assert argv[argv.index("--max-time") + 1] == "15"
    assert argv[argv.index("--retry") + 1] == "3"
    assert argv[argv.index("--retry-delay") + 1] == "5"
    assert argv[argv.index("-o") + 1] == "/dev/null"
    assert argv[argv.index("-w") + 1] == "%{http_code}"
    assert "-L" not in argv
    assert "--location" not in argv
    # The origin and the path reach curl as its config on stdin, not on a command line.
    assert ORIGIN not in smoke.log.read_text()
    assert smoke.stdin_log.read_text().strip() == f'url = "{ORIGIN}/"'


@pytest.mark.parametrize(
    "status",
    ["400", "401", "403", "404", "429", "500", "502", "503", "199", "100", "400\n200", "000"],
)
def test_public_smoke_fails_on_4xx_5xx_and_on_a_curl_failure(
    smoke: SmokeHarness, status: str
) -> None:
    smoke.code(status)
    result = smoke.run(ORIGIN)
    assert result.returncode != 0
    assert result.stdout == ""
    _assert_never_prints(result, ORIGIN)
    assert result.stderr.count("\n") == 1, result.stderr
    assert len(smoke.calls()) == 1


@pytest.mark.parametrize("status", ["", "ok", "20", "2000", "2xx", " 200", "200 ", "２００"])
def test_public_smoke_fails_on_a_status_that_is_not_three_digits(
    smoke: SmokeHarness, status: str
) -> None:
    smoke.code(status)
    result = smoke.run(ORIGIN)
    assert result.returncode != 0
    assert result.stdout == ""
    _assert_never_prints(result, ORIGIN)


def test_public_smoke_names_the_status_without_the_origin(smoke: SmokeHarness) -> None:
    smoke.code("503")
    result = smoke.run(ORIGIN)
    assert result.stderr == "the console did not answer with 2xx or 3xx (status 503)\n"


def test_public_smoke_fails_when_curl_fails(smoke: SmokeHarness) -> None:
    smoke.flag("fail-curl")
    result = smoke.run(ORIGIN)
    assert result.returncode != 0
    assert result.stdout == ""
    _assert_never_prints(result, ORIGIN)
    assert result.stderr.count("\n") == 1, result.stderr
    assert len(smoke.calls()) == 1


BAD_ORIGINS = [
    pytest.param("", id="empty"),
    pytest.param("console.example.test", id="no-scheme"),
    pytest.param("http://console.example.test", id="http"),
    pytest.param("HTTPS://console.example.test", id="uppercase-scheme"),
    pytest.param(ORIGIN + "/", id="trailing-slash"),
    pytest.param(ORIGIN + "/login", id="path"),
    pytest.param(ORIGIN + ":8443", id="port"),
    pytest.param(ORIGIN + "?x=1", id="query"),
    pytest.param("https://user@console.example.test", id="userinfo"),
    pytest.param("https://console.example.test extra", id="trailing-text"),
    pytest.param("https://console.example.test\n", id="trailing-newline"),
    pytest.param("https://console.example.test\nhttps://other.example.test", id="two-lines"),
    pytest.param("https://", id="no-host"),
    pytest.param('https://console.example.test"\nheader = "x', id="config-injection"),
    pytest.param("https://cönsole.example.test", id="non-ascii"),
    pytest.param("https://console.example.test٣", id="arabic-indic-digit"),
]


@pytest.mark.parametrize("bad", BAD_ORIGINS)
def test_public_smoke_refuses_a_malformed_origin_without_calling_curl(
    smoke: SmokeHarness, bad: str
) -> None:
    result = smoke.run(bad)
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1, result.stderr
    assert "console" not in result.stderr
    assert "example" not in result.stderr
    assert smoke.calls() == []


def test_public_smoke_needs_exactly_one_origin_argument(smoke: SmokeHarness) -> None:
    for args in ((), (ORIGIN, ORIGIN)):
        result = smoke.run(*args)
        assert result.returncode != 0
        assert result.stdout == ""
        _assert_never_prints(result, ORIGIN)
    assert smoke.calls() == []


def test_public_smoke_never_prints_the_origin(smoke: SmokeHarness) -> None:
    secret = "https://very-secret-host.example.test"
    for status in ("200", "302", "404", "503", "garbage"):
        smoke.code(status)
        result = smoke.run(secret)
        assert "secret" not in result.stdout + result.stderr
        assert "example" not in result.stdout + result.stderr
    smoke.flag("fail-curl")
    result = smoke.run(secret)
    assert result.returncode != 0
    assert "secret" not in result.stdout + result.stderr
    for bad in (secret + "/path", "http://very-secret-host.example.test"):
        result = smoke.run(bad)
        assert result.returncode != 0
        assert "secret" not in result.stdout + result.stderr
