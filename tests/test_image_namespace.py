"""The registry prefix of the database and app images comes from one repository variable.

CI, the publish workflow and the production resolve script read `IMAGE_NAMESPACE` (for example
`ghcr.io/example-owner`) instead of naming an owner, so moving the repository to another owner
changes one variable and no file.
"""

from __future__ import annotations

import re
from pathlib import Path

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


def test_publish_requires_a_lowercase_namespace() -> None:
    text = PUBLISH.read_text()
    steps = re.split(r"^      - ", text, flags=re.MULTILINE)[1:]
    first_skopeo = next(i for i, step in enumerate(steps) if "skopeo" in step)
    checks = [
        i
        for i, step in enumerate(steps)
        if "name: Require a lowercase image namespace" in step
        and NAMESPACE_PATTERN in step
        and NAMESPACE_MESSAGE in step
        and "$REGISTRY_NAMESPACE" in step
        and "exit 1" in step
    ]
    assert len(checks) == 1
    assert checks[0] == 0 or checks[0] < first_skopeo


def test_production_plan_passes_the_variable_to_the_resolve_script() -> None:
    text = DEPLOY_PRODUCTION.read_text()
    assert "IMAGE_NAMESPACE: ${{ vars.IMAGE_NAMESPACE }}" in text


def test_gitleaks_step_passes_the_optional_licence() -> None:
    steps = re.split(r"^      - ", CI.read_text(), flags=re.MULTILINE)[1:]
    gitleaks = [step for step in steps if "uses: gitleaks/gitleaks-action" in step]
    assert len(gitleaks) == 1
    assert "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in gitleaks[0]
    assert "GITLEAKS_LICENSE: ${{ secrets.GITLEAKS_LICENSE }}" in gitleaks[0]
