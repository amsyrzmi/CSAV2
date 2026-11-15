"""The ci workflow cancels a run that a newer run supersedes, and never cancels a manual run.

The group expression is evaluated here with a small model of the GitHub expression subset it
uses (`&&`, `||`, `==`, `!=`, `format`, context lookups), so the test checks what the three events
get as a group, not only that the text mentions them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
GROUP_EXPRESSION = (
    "ci-${{ github.event_name == 'pull_request'"
    " && format('pr-{0}', github.event.pull_request.number)"
    " || github.event_name == 'push' && 'main' || github.run_id }}"
)
CANCEL_EXPRESSION = "${{ github.event_name != 'workflow_dispatch' }}"


class _Context:
    """A context object whose missing members are null, as in GitHub expressions."""

    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    def __getattr__(self, name: str) -> Any:
        value = self._values.get(name)
        return _Context(value) if isinstance(value, dict) else value


def _concurrency() -> dict[str, Any]:
    workflow = yaml.safe_load(CI.read_text())
    assert "concurrency" in workflow, "ci.yml needs a workflow-level concurrency block"
    return workflow["concurrency"]


def _evaluate(template: str, github: dict[str, Any]) -> str:
    def render(match: re.Match[str]) -> str:
        source = match.group(1).strip()
        source = source.replace("&&", " and ").replace("||", " or ")
        value = eval(  # noqa: S307 - the expression comes from the repository's own workflow file
            source,
            {"__builtins__": {}},
            {"github": _Context(github), "format": lambda f, *a: f.format(*a)},
        )
        return "" if value is None else str(value)

    return re.sub(r"\$\{\{(.*?)\}\}", render, template)


def _group(event: str, *, run_id: int, pr: int | None = None) -> str:
    github: dict[str, Any] = {"event_name": event, "run_id": run_id, "event": {}}
    if pr is not None:
        github["event"] = {"pull_request": {"number": pr}}
    return _evaluate(_concurrency()["group"], github)


def test_concurrency_is_one_workflow_level_block() -> None:
    block = _concurrency()
    assert set(block) == {"group", "cancel-in-progress"}
    assert block["group"] == GROUP_EXPRESSION
    assert block["cancel-in-progress"] == CANCEL_EXPRESSION


def test_group_expression_names_the_three_cases() -> None:
    group = _concurrency()["group"]
    assert "github.event_name == 'pull_request'" in group
    assert "github.event.pull_request.number" in group
    assert "github.event_name == 'push'" in group
    assert "github.run_id" in group


def test_pull_requests_group_by_number() -> None:
    assert _group("pull_request", run_id=1, pr=5) == _group("pull_request", run_id=2, pr=5)
    assert _group("pull_request", run_id=1, pr=5) != _group("pull_request", run_id=1, pr=6)


def test_pushes_to_main_share_one_group() -> None:
    assert _group("push", run_id=21) == _group("push", run_id=22) == "ci-main"


def test_manual_runs_never_share_a_group() -> None:
    assert _group("workflow_dispatch", run_id=11) != _group("workflow_dispatch", run_id=12)


def test_groups_of_different_events_never_collide() -> None:
    groups = {
        _group("push", run_id=11),
        _group("pull_request", run_id=11, pr=5),
        _group("pull_request", run_id=11, pr=11),
        _group("workflow_dispatch", run_id=11),
    }
    assert len(groups) == 4


def test_only_manual_runs_are_not_cancelled() -> None:
    expression = _concurrency()["cancel-in-progress"]
    assert _evaluate(expression, {"event_name": "workflow_dispatch"}) == "False"
    assert _evaluate(expression, {"event_name": "push"}) == "True"
    assert _evaluate(expression, {"event_name": "pull_request"}) == "True"
