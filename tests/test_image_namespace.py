"""The registry prefix of the database and app images comes from one repository variable.

CI, the publish workflow and the production resolve script read `IMAGE_NAMESPACE` (for example
`ghcr.io/example-owner`) instead of naming an owner, so moving the repository to another owner
changes one variable and no file.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
PUBLISH = WORKFLOWS / "publish-staging-images.yml"
DEPLOY_PRODUCTION = WORKFLOWS / "deploy-production.yml"
NAMESPACE_PATTERN = r"^ghcr\.io/[a-z0-9][a-z0-9-]*$"
NAMESPACE_MESSAGE = "IMAGE_NAMESPACE must be set to ghcr.io/<lowercase owner>"


def test_workflows_name_no_hardcoded_namespace() -> None:
    for path in (CI, PUBLISH, DEPLOY_PRODUCTION):
        text = path.read_text()
        assert "ghcr.io/amsyrzmi" not in text, path.name
        assert not re.search(r"ghcr\.io/\$\{\{\s*github\.repository_owner", text), path.name
        assert not re.search(r"REGISTRY_NAMESPACE:.*repository_owner", text), path.name


def test_ci_images_use_the_variable() -> None:
    prefix = "${{ vars.IMAGE_NAMESPACE }}/csa-pg16-postgis-vector@sha256:"
    references = re.findall(
        r"^\s*(?:CSA_DB_IMAGE|image):\s*(.+?)\s*$", CI.read_text(), re.MULTILINE
    )
    database = [ref for ref in references if "csa-pg16-postgis-vector" in ref]
    assert len(database) == 3
    for ref in database:
        assert ref.startswith(prefix), ref
        assert re.fullmatch(r"[0-9a-f]{64}", ref.removeprefix(prefix)), ref


def test_publish_takes_its_namespace_from_the_variable() -> None:
    assert "REGISTRY_NAMESPACE: ${{ vars.IMAGE_NAMESPACE }}" in PUBLISH.read_text()


def _steps(path: Path) -> list[dict[str, Any]]:
    workflow = yaml.safe_load(path.read_text())
    return [step for job in workflow["jobs"].values() for step in job["steps"]]


def _namespace_step() -> dict[str, Any]:
    found = [s for s in _steps(PUBLISH) if s.get("name") == "Require a lowercase image namespace"]
    assert len(found) == 1
    return found[0]


def test_the_namespace_check_runs_before_the_first_registry_call() -> None:
    steps = _steps(PUBLISH)
    first_skopeo = next(i for i, step in enumerate(steps) if "skopeo" in str(step.get("run", "")))
    names = [step.get("name") for step in steps]
    assert names.count("Require a lowercase image namespace") == 1
    check = names.index("Require a lowercase image namespace")
    assert check < first_skopeo


# The step receives the value from the job env as REGISTRY_NAMESPACE (see the workflow's `env:`).
BAD_NAMESPACES = [
    "",
    "ghcr.io/Example-Owner",
    "ghcr.io/example-owner/",
    "https://ghcr.io/example-owner",
    "docker.io/example-owner",
    "ghcr.io/",
    "ghcr.io/-x",
    "ghcr.io/example-owner\n",
    "ghcr.io/a/b",
    "ghcr.io/ex\u0663",
    "ghcr.io/ex\u00e9",
]


def _run_namespace_step(namespace: str, locale: str) -> subprocess.CompletedProcess[str]:
    script = _namespace_step()["run"]
    env = {
        "PATH": os.environ["PATH"],
        "LC_ALL": locale,
        "LANG": locale,
        "REGISTRY_NAMESPACE": namespace,
    }
    return subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize("locale", ["C", "en_US.utf8"])
@pytest.mark.parametrize("namespace", BAD_NAMESPACES)
def test_the_publish_step_refuses_a_bad_namespace(namespace: str, locale: str) -> None:
    result = _run_namespace_step(namespace, locale)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == NAMESPACE_MESSAGE + "\n"


@pytest.mark.parametrize("locale", ["C", "en_US.utf8"])
@pytest.mark.parametrize("namespace", ["ghcr.io/example-owner", "ghcr.io/a-1"])
def test_the_publish_step_accepts_a_good_namespace(namespace: str, locale: str) -> None:
    result = _run_namespace_step(namespace, locale)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_production_plan_passes_the_variable_to_the_resolve_script() -> None:
    text = DEPLOY_PRODUCTION.read_text()
    assert "IMAGE_NAMESPACE: ${{ vars.IMAGE_NAMESPACE }}" in text


def test_gitleaks_step_passes_the_optional_licence() -> None:
    gitleaks = [
        s for s in _steps(CI) if str(s.get("uses", "")).startswith("gitleaks/gitleaks-action")
    ]
    assert len(gitleaks) == 1
    assert gitleaks[0]["env"] == {
        "GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}",
        "GITLEAKS_LICENSE": "${{ secrets.GITLEAKS_LICENSE }}",
    }
