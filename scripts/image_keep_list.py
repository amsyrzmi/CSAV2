"""Read-only keep list for old registry images: what is safe to delete, and why the rest is not.

Reads the versions of ``csa-python`` and ``csa-frontend`` and the staging deployments, and prints
one table per package: every version is ``KEEP(newest)``, ``KEEP(staged)``, ``KEEP(production)``,
``KEEP(extra)``, ``DELETE`` or ``REVIEW``. It never calls a delete endpoint, never takes a delete
flag, and never lists the database image package (it cannot be rebuilt). The owner runs any
deletion, with a token that has ``delete:packages``, from the DELETE rows.

Kept: the newest N SHA-tagged versions (and any other version at least as new as the Nth, which may
belong to one of them), every version tagged with a ``--production-sha`` (the SHA production runs
now and the one it ran before, which the script cannot read), every version tagged with a SHA that
one of the newest 15 staging deployments reached with a success status, and every ``--keep-sha``.
Only a SHA-tagged version outside that set is DELETE. A version with no SHA tag (untagged, or only
``latest`` and the like; an untagged one can be a child manifest of an index) is REVIEW: never a
candidate, the owner decides from its digest and tags.

    python -m scripts.image_keep_list --scope users/<owner> --production-sha SHA \
        [--production-sha SHA ...] [--keep-newest N] [--keep-sha SHA ...]
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
    """A version that stays, and why: newest, production, staged or extra (``--keep-sha``)."""

    version: Version
    reason: str


@dataclass(frozen=True)
class PrunePlan:
    """Every list is newest first; a version is in exactly one."""

    keep: tuple[Keep, ...]
    delete: tuple[Version, ...]
    review: tuple[Version, ...] = ()


def has_sha_tag(item: Version) -> bool:
    """True when a tag is a full lowercase commit SHA, the only tag the publish workflow writes."""
    return any(SHA_RE.fullmatch(tag) for tag in item.tags)


def plan_prune(
    versions: Sequence[Version],
    staged_shas: frozenset[str],
    keep_newest: int = DEFAULT_KEEP_NEWEST,
    *,
    production_shas: frozenset[str] = frozenset(),
    extra_shas: frozenset[str] = frozenset(),
) -> PrunePlan:
    """Split ``versions`` into what stays, what is a delete candidate and what needs review.

    Walking newest first, a SHA-tagged version is "newest" while fewer than ``keep_newest``
    SHA-tagged versions precede it, and so is any other version (it may be a manifest of a newer
    image). Past that, a version carrying a production, staged or extra SHA stays, a SHA-tagged
    one is a delete candidate, and one without a SHA tag is for review, never deleted.
    """
    ordered = sorted(versions, key=lambda item: (item.created_at, item.id), reverse=True)
    keep: list[Keep] = []
    delete: list[Version] = []
    review: list[Version] = []
    sha_tagged_seen = 0
    for item in ordered:
        newest = sha_tagged_seen < keep_newest
        if has_sha_tag(item):
            sha_tagged_seen += 1
        tags = set(item.tags)
        if newest:
            keep.append(Keep(item, "newest"))
        elif production_shas & tags:
            keep.append(Keep(item, "production"))
        elif staged_shas & tags:
            keep.append(Keep(item, "staged"))
        elif extra_shas & tags:
            keep.append(Keep(item, "extra"))
        elif has_sha_tag(item):
            delete.append(item)
        else:
            review.append(item)
    return PrunePlan(tuple(keep), tuple(delete), tuple(review))


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
    unique: dict[int, Version] = {}
    for raw in parse_pages(output, "package listing"):
        item = _version(raw)
        unique.setdefault(item.id, item)  # offset paging can repeat an item: first wins
    return list(unique.values())


@dataclass(frozen=True)
class Staged:
    """SHAs of the newest staging deployments with a success status, and their dates."""

    shas: frozenset[str]
    dates: tuple[str, ...]  # created_at of those deployments, oldest first


def read_staged(runner: Runner, repo: str) -> Staged:
    """The newest staging deployments that have a success status."""
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
    dates: list[str] = []
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
            dates.append(str(deployment.get("created_at", "")))
    return Staged(frozenset(staged), tuple(sorted(d for d in dates if d)))


def format_table(package: str, versions: Sequence[Version], plan: PrunePlan) -> list[str]:
    """One table, newest first: id, digest, tags, created, decision. KEEP always wins."""
    verdicts: dict[int, str] = {item.id: "DELETE" for item in plan.delete}
    verdicts.update({item.id: "REVIEW" for item in plan.review})
    verdicts.update({keep.version.id: f"KEEP({keep.reason})" for keep in plan.keep})
    lines = [
        f"{package}: {len(versions)} versions, {len(plan.keep)} kept, "
        f"{len(plan.delete)} to delete, {len(plan.review)} to review",
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
    if not value.isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError("must be a whole number of at least 1")
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
    parser.add_argument(
        "--production-sha",
        type=_sha,
        action="append",
        required=True,
        help="a SHA production runs now or ran before (repeat for both; read them yourself)",
    )
    parser.add_argument(
        "--keep-sha", type=_sha, action="append", default=[], help="an extra SHA to keep"
    )
    args = parser.parse_args(argv)
    report = err or (lambda line: print(line, file=sys.stderr))
    runner = runner or SubprocessRunner()
    try:
        listings = {name: list_versions(runner, args.scope, name) for name in PACKAGES}
        staged = read_staged(runner, repo)
    except RolloutError as error:
        report(f"image keep list: {error.message}")
        return 1
    production = frozenset(args.production_sha)
    extra = frozenset(args.keep_sha)
    out(f"Kept by --production-sha: {len(production)} SHAs; by --keep-sha: {len(extra)} SHAs")
    if staged.shas:
        span = f"{staged.dates[0]} to {staged.dates[-1]}" if staged.dates else "undated"
        out(f"Staged: {len(staged.shas)} SHAs from deployments dated {span}")
    else:
        out(
            "WARNING: no staged deployment found; only the newest "
            f"{args.keep_newest} and the --production-sha/--keep-sha versions are protected"
        )
    out("")
    for name, versions in listings.items():
        plan = plan_prune(
            versions,
            staged.shas,
            args.keep_newest,
            production_shas=production,
            extra_shas=extra,
        )
        for line in format_table(name, versions, plan):
            out(line)
        out("")
    out("REVIEW rows have no SHA tag and are never proposed for deletion.")
    out("Nothing was deleted: this script only reads.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
