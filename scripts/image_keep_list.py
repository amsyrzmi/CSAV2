"""Read-only keep list for old registry images: what is safe to delete, and why the rest is not.

Reads the versions of ``csa-python`` and ``csa-frontend`` and the staging deployments, and prints
one table per package: every version is ``KEEP(newest)``, ``KEEP(staged)`` or ``DELETE``. It
never calls a delete endpoint, never takes a delete flag, and never lists the database image
package (it cannot be rebuilt). The owner runs any deletion, with a token that has
``delete:packages``, from the DELETE rows.

Kept: the newest N SHA-tagged versions (and any untagged version at least as new as the Nth, which
may belong to one of them), every version tagged with a SHA that a staging deployment (one of the
newest 15) reached with a success status, and every ``--keep-sha``. Everything else is a candidate.

    python -m scripts.image_keep_list --scope users/<owner> [--keep-newest N] [--keep-sha SHA ...]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from scripts.rollout_targets import STAGING
from scripts.staging_rollout import (
    GATE_ENVIRONMENT,
    SHA_RE,
    RolloutError,
    Runner,
    SubprocessRunner,
    _gh,
)

PACKAGES = ("csa-python", "csa-frontend")  # never the database image package
DEFAULT_KEEP_NEWEST = 20
STAGED_DEPLOYMENTS = 15  # the newest staging deployments whose SHAs count as staged
SCOPE_RE = re.compile(r"^(?:users|orgs)/[A-Za-z0-9][A-Za-z0-9-]*$")
STEP = "keep-list"


@dataclass(frozen=True)
class Version:
    """One package version: ``name`` is its digest."""

    id: int
    name: str
    created_at: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Keep:
    """A version that stays, and why: ``"newest"`` or ``"staged"``."""

    version: Version
    reason: str


@dataclass(frozen=True)
class PrunePlan:
    """Both lists are newest first."""

    keep: tuple[Keep, ...]
    delete: tuple[Version, ...]


def plan_prune(
    versions: Sequence[Version],
    staged_shas: frozenset[str],
    keep_newest: int = DEFAULT_KEEP_NEWEST,
) -> PrunePlan:
    """Split ``versions`` into what stays and what is a delete candidate.

    Walking newest first, a tagged version is "newest" while fewer than ``keep_newest`` tagged
    versions precede it, and so is an untagged one (it may be a manifest of a newer tagged
    image). Any version carrying a staged tag stays whatever its age.
    """
    ordered = sorted(versions, key=lambda item: (item.created_at, item.id), reverse=True)
    keep: list[Keep] = []
    delete: list[Version] = []
    tagged_seen = 0
    for item in ordered:
        newest = tagged_seen < keep_newest
        if item.tags:
            tagged_seen += 1
        if newest:
            keep.append(Keep(item, "newest"))
        elif staged_shas.intersection(item.tags):
            keep.append(Keep(item, "staged"))
        else:
            delete.append(item)
    return PrunePlan(tuple(keep), tuple(delete))


def _unreadable(what: str) -> RolloutError:
    return RolloutError(STEP, f"gh returned an unexpected {what}")


def parse_pages(text: str, what: str) -> list[object]:
    """The items of ``gh api --paginate`` output: JSON arrays printed back to back."""
    decoder = json.JSONDecoder()
    items: list[object] = []
    position = 0
    while True:
        while position < len(text) and text[position].isspace():
            position += 1
        if position >= len(text):
            return items
        try:
            page, position = decoder.raw_decode(text, position)
        except json.JSONDecodeError as error:
            raise _unreadable(what) from error
        if not isinstance(page, list):
            raise _unreadable(what)
        items.extend(page)


def _version(raw: object) -> Version:
    if not isinstance(raw, dict):
        raise _unreadable("package listing")
    number, name, created = raw.get("id"), raw.get("name"), raw.get("created_at")
    metadata = raw.get("metadata")
    container = metadata.get("container") if isinstance(metadata, dict) else None
    tags = container.get("tags") if isinstance(container, dict) else None
    if (
        type(number) is not int
        or not isinstance(name, str)
        or not isinstance(created, str)
        or not isinstance(tags, list)
        or not all(isinstance(tag, str) for tag in tags)
    ):
        raise _unreadable("package listing")
    return Version(number, name, created, tuple(tags))


def list_versions(runner: Runner, scope: str, package: str) -> list[Version]:
    """Every version of one package, across all pages."""
    path = f"/{scope}/packages/container/{package}/versions?per_page=100"
    output = _gh(runner, ["gh", "api", "--paginate", path], STEP)
    return [_version(raw) for raw in parse_pages(output, "package listing")]


def staged_shas(runner: Runner, repo: str) -> frozenset[str]:
    """SHAs of the newest staging deployments that have a success status."""
    base = f"repos/{repo}/deployments"
    query = f"environment={GATE_ENVIRONMENT}&per_page={STAGED_DEPLOYMENTS}"
    try:
        listing = json.loads(_gh(runner, ["gh", "api", f"{base}?{query}"], STEP))
    except json.JSONDecodeError as error:
        raise _unreadable("deployment listing") from error
    if not isinstance(listing, list) or not all(isinstance(item, dict) for item in listing):
        raise _unreadable("deployment listing")
    newest = sorted(listing, key=lambda item: str(item.get("created_at", "")), reverse=True)
    staged: set[str] = set()
    for deployment in newest[:STAGED_DEPLOYMENTS]:
        number, commit = deployment.get("id"), deployment.get("sha")
        if type(number) is not int or not isinstance(commit, str) or not SHA_RE.fullmatch(commit):
            raise _unreadable("deployment listing")
        try:
            statuses = json.loads(
                _gh(runner, ["gh", "api", f"{base}/{number}/statuses?per_page=100"], STEP)
            )
        except json.JSONDecodeError as error:
            raise _unreadable("deployment status listing") from error
        if not isinstance(statuses, list) or not all(isinstance(s, dict) for s in statuses):
            raise _unreadable("deployment status listing")
        if any(status.get("state") == "success" for status in statuses):
            staged.add(commit)
    return frozenset(staged)


def format_table(package: str, versions: Sequence[Version], plan: PrunePlan) -> list[str]:
    """One table, newest first: id, digest, tags, created, decision."""
    verdicts: dict[int, str] = {keep.version.id: f"KEEP({keep.reason})" for keep in plan.keep}
    verdicts.update({item.id: "DELETE" for item in plan.delete})
    lines = [
        f"{package}: {len(versions)} versions, {len(plan.keep)} kept, {len(plan.delete)} to delete",
        "id\tdigest\ttags\tcreated\tdecision",
    ]
    for item in sorted(versions, key=lambda v: (v.created_at, v.id), reverse=True):
        tags = ",".join(item.tags) or "-"
        lines.append(f"{item.id}\t{item.name}\t{tags}\t{item.created_at}\t{verdicts[item.id]}")
    return lines


def _scope(value: str) -> str:
    if not SCOPE_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("must look like users/<name> or orgs/<name>")
    return value


def _sha(value: str) -> str:
    if not SHA_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("must be a full lowercase 40-character SHA")
    return value


def _count(value: str) -> int:
    if not value.isdigit():
        raise argparse.ArgumentTypeError("must be a whole number")
    return int(value)


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner | None = None,
    out: Callable[[str], object] = print,
    err: Callable[[str], object] | None = None,
    repo: str = STAGING.repo,
) -> int:
    """Print the keep list; exit 0, or 1 with a message when GitHub's answer is unusable."""
    parser = argparse.ArgumentParser(prog="python -m scripts.image_keep_list", description=__doc__)
    parser.add_argument("--scope", type=_scope, default=STAGING.package_scope)
    parser.add_argument("--keep-newest", type=_count, default=DEFAULT_KEEP_NEWEST)
    parser.add_argument("--keep-sha", type=_sha, action="append", default=[])
    args = parser.parse_args(argv)
    report = err or (lambda line: print(line, file=sys.stderr))
    runner = runner or SubprocessRunner()
    try:
        listings = {name: list_versions(runner, args.scope, name) for name in PACKAGES}
        keep_shas = staged_shas(runner, repo) | frozenset(args.keep_sha)
    except RolloutError as error:
        report(f"image keep list: {error.message}")
        return 1
    for name, versions in listings.items():
        plan = plan_prune(versions, keep_shas, args.keep_newest)
        for line in format_table(name, versions, plan):
            out(line)
        out("")
    out("Nothing was deleted: this script only reads.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
