"""Read-only keep list for old registry images: the pure plan and the thin command."""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest
from scripts import image_keep_list as ikl
from scripts.image_keep_list import Version, plan_prune
from scripts.staging_rollout import Completed

from tests.test_staging_rollout import FakeRunner, fail, has, ok

SCOPE = "users/example-owner"
REPO = "example-owner/example-repo"


def sha(number: int) -> str:
    return f"{number:040x}"


def digest(number: int) -> str:
    return f"sha256:{number:064x}"


def version(number: int, *tags: str, day: int | None = None) -> Version:
    """Version ``number``; a higher number is newer unless ``day`` says otherwise."""
    return Version(
        id=1000 + number,
        name=digest(number),
        created_at=f"2026-01-{(day or number):02d}T00:00:00Z",
        tags=tags,
    )


def tagged(count: int) -> list[Version]:
    return [version(n, sha(n)) for n in range(1, count + 1)]


def kept_ids(plan: ikl.PrunePlan) -> set[int]:
    return {keep.version.id for keep in plan.keep}


def deleted_ids(plan: ikl.PrunePlan) -> set[int]:
    return {item.id for item in plan.delete}


def test_the_newest_n_are_kept_and_the_rest_are_candidates():
    versions = tagged(25)
    plan = plan_prune(versions, frozenset(), keep_newest=20)
    assert deleted_ids(plan) == {1000 + n for n in range(1, 6)}
    assert kept_ids(plan) == {1000 + n for n in range(6, 26)}
    assert {keep.reason for keep in plan.keep} == {"newest"}


def test_the_default_keeps_the_newest_twenty():
    plan = plan_prune(tagged(23), frozenset())
    assert len(plan.keep) == 20
    assert len(plan.delete) == 3


def test_a_staged_sha_is_kept_even_when_older_than_the_newest_n():
    versions = tagged(25)
    plan = plan_prune(versions, frozenset({sha(2)}), keep_newest=20)
    assert 1002 in kept_ids(plan)
    assert deleted_ids(plan) == {1001, 1003, 1004, 1005}
    reasons = {keep.version.id: keep.reason for keep in plan.keep}
    assert reasons[1002] == "staged"
    assert reasons[1025] == "newest"


def test_a_version_that_is_both_newest_and_staged_is_reported_as_newest():
    plan = plan_prune(tagged(5), frozenset({sha(5)}), keep_newest=3)
    reasons = {keep.version.id: keep.reason for keep in plan.keep}
    assert reasons[1005] == "newest"


def test_a_version_with_two_tags_and_one_staged_tag_is_kept():
    versions = [version(n, sha(n)) for n in range(2, 12)]
    versions.append(version(1, sha(1), sha(900)))
    plan = plan_prune(versions, frozenset({sha(900)}), keep_newest=5)
    assert 1001 in kept_ids(plan)
    assert 1001 not in deleted_ids(plan)


def test_fewer_versions_than_n_gives_no_delete_candidates():
    plan = plan_prune(tagged(7), frozenset(), keep_newest=20)
    assert plan.delete == ()
    assert len(plan.keep) == 7


def test_exactly_n_versions_gives_no_delete_candidates():
    plan = plan_prune(tagged(20), frozenset(), keep_newest=20)
    assert plan.delete == ()


def review_ids(plan: ikl.PrunePlan) -> set[int]:
    return {item.id for item in plan.review}


def test_an_untagged_version_outside_the_newest_n_is_for_review_never_delete():
    versions = [*tagged(6), version(0)]  # untagged and the oldest
    plan = plan_prune(versions, frozenset(), keep_newest=5)
    assert deleted_ids(plan) == {1001}
    assert review_ids(plan) == {1000}


@pytest.mark.parametrize("tags", [("latest",), ("abc1234",), ("bootstrap-2026",), (f"{10:040X}",)])
def test_a_version_with_only_non_sha_tags_outside_the_newest_n_is_for_review(tags: tuple[str, ...]):
    versions = [*tagged(6), version(0, *tags)]
    plan = plan_prune(versions, frozenset(), keep_newest=5)
    assert review_ids(plan) == {1000}
    assert 1000 not in deleted_ids(plan)


def test_a_sha_tag_beside_latest_outside_the_keep_set_is_delete():
    versions = [*tagged(6), version(0, sha(0), "latest")]
    plan = plan_prune(versions, frozenset(), keep_newest=5)
    assert deleted_ids(plan) == {1000, 1001}
    assert plan.review == ()


def test_a_staged_or_production_sha_beside_latest_is_kept():
    versions = [*tagged(8), version(0, sha(0), "latest")]
    plan = plan_prune(versions, frozenset({sha(0)}), keep_newest=5)
    assert 1000 in kept_ids(plan)


def test_a_non_sha_tagged_version_newer_than_the_cut_is_kept():
    versions = [*tagged(6), version(7, "latest", day=28)]
    plan = plan_prune(versions, frozenset(), keep_newest=5)
    assert 1007 in kept_ids(plan)
    assert plan.review == ()


def test_an_untagged_version_newer_than_the_cut_is_kept():
    # It may be a layer manifest of a newer tagged image: never a candidate.
    versions = [*tagged(6), version(7, day=28)]  # untagged, newest of all
    plan = plan_prune(versions, frozenset(), keep_newest=5)
    assert 1007 in kept_ids(plan)
    assert 1007 not in deleted_ids(plan)
    assert 1007 not in review_ids(plan)
    assert deleted_ids(plan) == {1001}


def test_untagged_versions_do_not_crowd_the_newest_tagged_ones_out():
    versions = [*tagged(5), *[version(n, day=10 + n) for n in range(40, 50)]]
    plan = plan_prune(versions, frozenset(), keep_newest=5)
    assert plan.delete == ()


def test_an_untagged_staged_sha_is_not_a_thing_and_an_unstaged_old_version_goes():
    versions = tagged(8)
    plan = plan_prune(versions, frozenset({sha(999)}), keep_newest=5)
    assert deleted_ids(plan) == {1001, 1002, 1003}


def test_keep_and_delete_are_ordered_newest_first_regardless_of_input_order():
    versions = [version(3, sha(3)), version(9, sha(9)), version(1, sha(1)), version(5, sha(5))]
    plan = plan_prune(versions, frozenset(), keep_newest=2)
    assert [keep.version.id for keep in plan.keep] == [1009, 1005]
    assert [item.id for item in plan.delete] == [1003, 1001]


def test_a_version_tagged_with_a_staged_sha_is_never_a_candidate_across_many_shapes():
    versions = tagged(130)
    staged = frozenset({sha(1), sha(60), sha(129)})
    plan = plan_prune(versions, staged, keep_newest=20)
    for item in plan.delete:
        assert not (set(item.tags) & staged)
    newest = {v.id for v in sorted(versions, key=lambda v: v.created_at)[-20:]}
    assert newest.isdisjoint(deleted_ids(plan))
    assert len(plan.keep) + len(plan.delete) == 130


def test_every_version_lands_in_exactly_one_list():
    versions = [*tagged(12), version(30), version(31, sha(31), sha(32))]
    plan = plan_prune(versions, frozenset({sha(3)}), keep_newest=4)
    assert kept_ids(plan).isdisjoint(deleted_ids(plan))
    assert kept_ids(plan).isdisjoint(review_ids(plan))
    assert deleted_ids(plan).isdisjoint(review_ids(plan))
    assert kept_ids(plan) | deleted_ids(plan) | review_ids(plan) == {v.id for v in versions}


def test_no_versions_gives_an_empty_plan():
    plan = plan_prune([], frozenset())
    assert plan.keep == ()
    assert plan.delete == ()
    assert plan.review == ()


def test_production_and_extra_shas_are_kept_with_their_own_reasons():
    plan = plan_prune(
        tagged(30),
        frozenset({sha(3)}),
        keep_newest=5,
        production_shas=frozenset({sha(1), sha(3)}),
        extra_shas=frozenset({sha(2), sha(26)}),
    )
    reasons = {keep.version.id: keep.reason for keep in plan.keep}
    assert reasons[1001] == "production"
    assert reasons[1003] == "production"  # production wins over staged
    assert reasons[1002] == "extra"
    assert reasons[1026] == "newest"  # newest wins over extra
    assert reasons[1030] == "newest"
    assert 1004 in deleted_ids(plan)


def test_a_production_sha_older_than_everything_else_is_never_a_candidate():
    versions = tagged(60)
    plan = plan_prune(versions, frozenset(), production_shas=frozenset({sha(1)}))
    assert 1001 in kept_ids(plan)
    assert 1001 not in deleted_ids(plan)


# --- the command -------------------------------------------------------------------------------


def api_version(item: Version) -> dict[str, object]:
    return {
        "id": item.id,
        "name": item.name,
        "created_at": item.created_at,
        "metadata": {"package_type": "container", "container": {"tags": list(item.tags)}},
    }


def paginated(versions: Sequence[Version], page: int = 100, joiner: str = "\n") -> str:
    """What ``gh api --paginate`` prints: one JSON array per page, back to back."""
    chunks = [versions[i : i + page] for i in range(0, len(versions), page)]
    return joiner.join(json.dumps([api_version(v) for v in chunk], indent=1) for chunk in chunks)


def deployment(number: int, commit: str) -> dict[str, object]:
    return {
        "id": number,
        "sha": commit,
        "environment": "staging",
        "created_at": f"2026-02-{number:02d}T00:00:00Z",
    }


def runner_for(
    python: Sequence[Version],
    frontend: Sequence[Version],
    *,
    deployments: Sequence[dict[str, object]] = (),
    statuses: dict[int, list[str]] | None = None,
) -> FakeRunner:
    statuses = statuses or {}

    def status_answer(argv: Sequence[str], env: object) -> Completed:
        number = int(argv[-1].split("/deployments/")[1].split("/")[0])
        return ok(
            json.dumps(
                [
                    {"state": s, "created_at": "2026-02-01T00:00:00Z"}
                    for s in statuses.get(number, [])
                ]
            )
        )

    return FakeRunner(
        [
            (has("csa-python/versions"), ok(paginated(python))),
            (has("csa-frontend/versions"), ok(paginated(frontend))),
            (has("/statuses"), status_answer),
            (has(f"repos/{REPO}/deployments"), ok(json.dumps(list(deployments)))),
        ]
    )


PRODUCTION = ["--production-sha", sha(900), "--production-sha", sha(901)]


def run(runner: FakeRunner, *args: str, production: bool = True) -> tuple[int, str, str]:
    lines: list[str] = []
    errors: list[str] = []
    extra = PRODUCTION if production else []
    code = ikl.main(
        ["--scope", SCOPE, *extra, *args],
        runner=runner,
        out=lines.append,
        err=errors.append,
        repo=REPO,
    )
    return code, "\n".join(lines), "\n".join(errors)


def test_the_command_prints_one_table_per_package_with_keep_and_delete():
    python = tagged(23)
    frontend = tagged(3)
    runner = runner_for(python, frontend)
    code, out, _ = run(runner)
    assert code == 0
    assert "csa-python" in out
    assert "csa-frontend" in out
    assert out.count("DELETE") == 3
    assert out.count("KEEP(newest)") == 23
    assert digest(1) in out
    assert str(1001) in out
    assert sha(1) in out


def test_more_than_one_hundred_versions_are_read_across_pages():
    python = tagged(230)
    runner = runner_for(python, tagged(1))
    code, out, _ = run(runner)
    assert code == 0
    assert out.count("DELETE") == 210
    argv = next(a for a in runner.argvs if any("csa-python/versions" in x for x in a))
    assert "--paginate" in argv
    assert argv[:2] == ["gh", "api"]
    assert f"/{SCOPE}/packages/container/csa-python/versions?per_page=100" in argv


def test_a_staged_deployment_sha_older_than_the_newest_n_is_kept_by_the_command():
    python = tagged(30)
    runner = runner_for(
        python,
        tagged(1),
        deployments=[deployment(1, sha(2)), deployment(2, sha(3))],
        statuses={1: ["success"], 2: ["failure"]},
    )
    code, out, _ = run(runner)
    assert code == 0
    line = next(line for line in out.splitlines() if str(1002) in line and "csa" not in line)
    assert "KEEP(staged)" in line
    line = next(line for line in out.splitlines() if str(1003) in line and "csa" not in line)
    assert "DELETE" in line  # its only status was a failure: it never staged


def test_only_the_newest_fifteen_staging_deployments_are_read():
    deployments = [deployment(n, sha(n)) for n in range(1, 21)]
    statuses = {n: ["success"] for n in range(1, 21)}
    runner = runner_for(tagged(2), tagged(2), deployments=deployments, statuses=statuses)
    code, _, _ = run(runner)
    assert code == 0
    status_calls = [a for a in runner.argvs if any("/statuses" in x for x in a)]
    assert len(status_calls) == 15
    asked = {int(a[-1].split("/deployments/")[1].split("/")[0]) for a in status_calls}
    assert asked == set(range(6, 21))


def test_the_deployments_listing_asks_for_the_staging_environment():
    runner = runner_for(tagged(2), tagged(2))
    run(runner)
    listing = next(
        a for a in runner.argvs if any(x.startswith(f"repos/{REPO}/deployments") for x in a)
    )
    assert any("environment=staging" in x for x in listing)


def test_keep_sha_extras_are_kept():
    runner = runner_for(tagged(30), tagged(1))
    code, out, _ = run(runner, "--keep-sha", sha(4), "--keep-sha", sha(5))
    assert code == 0
    for number in (4, 5):
        line = next(x for x in out.splitlines() if str(1000 + number) in x and "csa" not in x)
        assert "KEEP(extra)" in line


def test_keep_newest_changes_the_cut():
    runner = runner_for(tagged(30), tagged(1))
    code, out, _ = run(runner, "--keep-newest", "28")
    assert code == 0
    assert out.count("DELETE") == 2


@pytest.mark.parametrize("option", ["--keep-sha", "--production-sha"])
@pytest.mark.parametrize("value", ["not-a-sha", f"{10:040X}", sha(1)[:39], ""])
def test_a_malformed_sha_is_refused_before_any_call(option: str, value: str):
    runner = runner_for(tagged(3), tagged(3))
    with pytest.raises(SystemExit) as stop:
        run(runner, option, value)
    assert stop.value.code == 2
    assert runner.calls == []


def test_production_sha_is_required(capsys: pytest.CaptureFixture[str]):
    runner = runner_for(tagged(3), tagged(3))
    with pytest.raises(SystemExit) as stop:
        run(runner, production=False)
    assert stop.value.code == 2
    assert "--production-sha" in capsys.readouterr().err
    assert runner.calls == []


@pytest.mark.parametrize("value", ["0", "-1", "x", "1.5"])
def test_keep_newest_below_one_is_refused_before_any_call(value: str):
    runner = runner_for(tagged(3), tagged(3))
    with pytest.raises(SystemExit) as stop:
        run(runner, "--keep-newest", value)
    assert stop.value.code == 2
    assert runner.calls == []


def test_production_shas_are_kept_with_their_reason_even_when_old():
    runner = runner_for(tagged(30), tagged(1))
    code, out, _ = run(
        runner, "--production-sha", sha(1), "--production-sha", sha(2), production=False
    )
    assert code == 0
    for number in (1, 2):
        line = next(x for x in out.splitlines() if str(1000 + number) in x and "csa" not in x)
        assert "KEEP(production)" in line
    assert out.count("DELETE") == 8  # 10 outside the newest 20, minus the two production


def test_review_rows_are_never_delete_and_show_digest_and_tags():
    python = [*tagged(25), version(0, "latest"), version(-1)]
    runner = runner_for(python, tagged(1))
    code, out, _ = run(runner)
    assert code == 0
    review = [x for x in out.splitlines() if x.endswith("REVIEW")]
    assert len(review) == 2
    assert any("latest" in x and digest(0) in x for x in review)
    assert out.count("DELETE") == 5


def test_duplicate_version_ids_from_paging_are_counted_once():
    python = [*tagged(25), version(6, sha(6))]  # a repeat of the 20th newest
    runner = runner_for(python, tagged(1))
    code, out, _ = run(runner)
    assert code == 0
    rows = [x for x in out.splitlines() if x.startswith("1006\t")]
    assert len(rows) == 1
    assert "KEEP(newest)" in rows[0]
    assert "csa-python: 25 versions" in out


def test_keep_wins_over_delete_in_the_table():
    item = version(1, sha(1))
    plan = ikl.PrunePlan(keep=(ikl.Keep(item, "newest"),), delete=(item,), review=())
    lines = ikl.format_table("csa-python", [item], plan)
    assert lines[-1].endswith("KEEP(newest)")


def test_pages_joined_without_a_separator_are_read():
    python = tagged(230)
    runner = FakeRunner(
        [
            (has("csa-python/versions"), ok(paginated(python, joiner=""))),
            (has("csa-frontend/versions"), ok(paginated(tagged(1)))),
            (has("/deployments"), ok("[]")),
        ]
    )
    assert "][" in paginated(python, joiner="")
    code, out, _ = run(runner)
    assert code == 0
    assert out.count("DELETE") == 210


def test_parse_pages_reads_arrays_back_to_back():
    assert ikl.parse_pages("[1,2][3]\n[4]", "x") == [1, 2, 3, 4]
    assert ikl.parse_pages("", "x") == []


def test_a_staged_summary_is_printed_above_the_tables():
    runner = runner_for(
        tagged(30),
        tagged(1),
        deployments=[deployment(1, sha(2)), deployment(3, sha(3)), deployment(2, sha(4))],
        statuses={1: ["success"], 2: ["failure"], 3: ["success"]},
    )
    code, out, _ = run(runner)
    assert code == 0
    lines = out.splitlines()
    summary = next(i for i, x in enumerate(lines) if x.startswith("Staged:"))
    table = next(i for i, x in enumerate(lines) if x.startswith("csa-python:"))
    assert summary < table
    assert "2 SHAs" in lines[summary]
    assert "2026-02-01" in lines[summary]
    assert "2026-02-03" in lines[summary]
    assert "WARNING" not in out


@pytest.mark.parametrize("answer", ["[]", None])
def test_no_staged_deployment_warns_above_the_tables_and_still_exits_zero(answer: str | None):
    runner = runner_for(
        tagged(30),
        tagged(1),
        deployments=[deployment(1, sha(2))] if answer is None else [],
        statuses={1: ["failure"]},
    )
    code, out, _ = run(runner)
    assert code == 0
    lines = out.splitlines()
    warning = next(i for i, x in enumerate(lines) if x.startswith("WARNING: no staged deployment"))
    assert "--production-sha/--keep-sha" in lines[warning]
    assert "newest 20" in lines[warning]
    assert warning < next(i for i, x in enumerate(lines) if x.startswith("csa-python:"))


@pytest.mark.parametrize(
    "scope", ["", "example-owner", "users/", "users/a b", "users/../x", "teams/x"]
)
def test_a_malformed_scope_is_refused_before_any_call(scope: str):
    runner = runner_for(tagged(3), tagged(3))
    with pytest.raises(SystemExit) as stop:
        ikl.main(
            ["--scope", scope, *PRODUCTION],
            runner=runner,
            out=lambda _: None,
            err=lambda _: None,
            repo=REPO,
        )
    assert stop.value.code == 2
    assert runner.calls == []


def test_the_default_scope_comes_from_the_staging_target():
    runner = runner_for(tagged(2), tagged(2))
    code = ikl.main(PRODUCTION, runner=runner, out=lambda _: None, err=lambda _: None, repo=REPO)
    assert code == 0
    assert any(
        f"/{ikl.STAGING.package_scope}/packages/container/csa-python" in " ".join(a)
        for a in runner.argvs
    )


def test_the_script_never_issues_a_delete():
    runner = runner_for(
        tagged(40),
        tagged(40),
        deployments=[deployment(1, sha(3))],
        statuses={1: ["success"]},
    )
    code, _, _ = run(runner, "--keep-sha", sha(1))
    assert code == 0
    assert runner.argvs
    for argv in runner.argvs:
        assert argv[:2] == ["gh", "api"]
        assert "DELETE" not in argv
        assert "-X" not in argv
        assert "--method" not in argv
        assert not any(part.upper() == "DELETE" for part in argv)


def test_the_database_image_package_is_never_listed():
    runner = runner_for(tagged(3), tagged(3))
    _, out, _ = run(runner)
    assert not any("pg16" in " ".join(a) or "postgis" in " ".join(a) for a in runner.argvs)
    assert "pg16" not in out
    assert ikl.PACKAGES == ("csa-python", "csa-frontend")


def test_a_gh_failure_stops_with_exit_one_and_a_message():
    runner = FakeRunner([(has("csa-python/versions"), fail(1, "HTTP 403: nope\nmore"))])
    code, out, err = run(runner)
    assert code == 1
    assert "HTTP 403: nope" in err
    assert "KEEP" not in out
    assert "DELETE" not in out


@pytest.mark.parametrize(
    "stdout",
    [
        "{}",
        "[1]",
        '[{"id": 1}]',
        "[{",
        '[{"id": "x", "name": "n", "created_at": "t", "metadata": {}}]',
    ],
)
def test_an_unexpected_listing_stops_instead_of_listing_anything(stdout: str):
    runner = FakeRunner([(has("csa-python/versions"), ok(stdout))])
    code, out, err = run(runner)
    assert code == 1
    assert err
    assert "DELETE" not in out


def test_an_unreadable_deployment_listing_stops_instead_of_guessing_what_is_staged():
    runner = FakeRunner(
        [
            (has("csa-python/versions"), ok(paginated(tagged(30)))),
            (has("csa-frontend/versions"), ok(paginated(tagged(30)))),
            (has("/deployments"), ok('{"message": "nope"}')),
        ]
    )
    code, out, err = run(runner)
    assert code == 1
    assert err
    assert "DELETE" not in out


def test_the_staged_shas_are_read_before_anything_is_printed():
    runner = FakeRunner(
        [
            (has("csa-python/versions"), ok(paginated(tagged(30)))),
            (has("csa-frontend/versions"), ok(paginated(tagged(30)))),
            (has("/deployments"), fail(1, "HTTP 500")),
        ]
    )
    code, out, _ = run(runner)
    assert code == 1
    assert out == ""
