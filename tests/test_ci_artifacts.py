"""The review image bundle is uploaded for a push to main, not for pull requests.

Pull requests still build the images, which catches Dockerfile breakage. Only the main run
uploads `csa-review-only-<sha>`, because the publish-staging-images workflow downloads it from
that run. The bundle is kept three days.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
PUBLISH = WORKFLOWS / "publish-staging-images.yml"
BUNDLE_NAME = "csa-review-only-${{ github.sha }}"


def _bundle_upload_step() -> dict[str, Any]:
    steps = yaml.safe_load(CI.read_text())["jobs"]["package"]["steps"]
    found = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert len(found) == 1
    return found[0]


def test_pull_requests_do_not_upload_the_bundle() -> None:
    assert _bundle_upload_step().get("if") == "github.event_name != 'pull_request'"


def test_the_main_run_still_uploads_the_bundle_the_publish_workflow_downloads() -> None:
    assert _bundle_upload_step()["with"]["name"] == BUNDLE_NAME
    steps = yaml.safe_load(PUBLISH.read_text())["jobs"]
    downloads = [
        step["with"]["name"]
        for job in steps.values()
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/download-artifact@")
    ]
    assert "csa-review-only-${{ steps.run.outputs.head_sha }}" in downloads


def test_the_bundle_is_kept_three_days() -> None:
    assert _bundle_upload_step()["with"]["retention-days"] == 3
    # The publish workflow runs right after the main run; three days is a comfortable margin.
    assert _bundle_upload_step()["with"]["retention-days"] >= 3
