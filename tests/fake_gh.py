"""A stateful fake ``gh`` (GitHub CLI) for the deploy scripts' tests.

The fake is a small Python program written into ``<tmp>/bin/gh``; that directory comes first on
``PATH`` when a script runs. It keeps a JSON state file of the repository, users, environments
(reviewers, protection settings, branch policies, secrets), repository variables, workflows and
their runs and jobs. It logs every call (argv and the byte length of stdin, never stdin content) and
answers like ``gh``: JSON on stdout, ``gh: <message> (HTTP <status>)`` on stderr, exit 1 for an
HTTP error, exit 4 when logged out.

What it models is what was observed on GitHub's environments API (2026-11-18):

* ``GET environments/<env>`` returns ``protection_rules`` (required reviewers, a wait timer only
  when it is above 0, a ``branch_policy`` entry when custom policies are on), ``can_admins_bypass``
  and ``deployment_branch_policy`` (``null`` or ``{protected_branches, custom_branch_policies}``).
* ``PUT environments/<env>`` has two modes, the state key ``put_semantics``. ``merge`` (the
  observed default): a partial body leaves the settings it does not mention untouched.
  ``replace`` (the pessimistic reading): a missing setting resets (reviewers empty, wait timer 0,
  self-review false, admin bypass true, no deployment branch policy). GitHub documents neither;
  the scripts' tests run under both. ``put_ignores`` names fields the PUT treats as absent from the
  body, so a test can prove a script re-reads after writing.
* Branch policies exist only while custom policies are on: the list and the POST of any other
  environment answer 404, as GitHub does for ``deployment_branch_policy: null``.

Workflow runs (read-only GETs of the real repository, 2026-11-18, and the REST documentation):

* ``GET repos/<r>/actions/workflows/<file>/runs`` returns ``{total_count, workflow_runs}``, newest
  first; each run has ``id``, ``name``, ``head_branch``, ``head_sha``, ``path``, ``display_title``,
  ``run_number``, ``event``, ``status``, ``conclusion`` (``null`` until completed), ``workflow_id``,
  ``url``, ``html_url``, ``created_at``, ``updated_at``, ``actor`` and ``triggering_actor``
  (``{login, id, type}``), ``run_attempt``, ``run_started_at``, ``jobs_url``, ``cancel_url``. The
  filters ``status``, ``event``, ``branch``, ``head_sha`` and ``actor`` are exact matches (verified:
  ``status`` matches a run's ``status`` or its ``conclusion``, so ``status=success`` lists the runs
  that concluded success; an unknown value or an unknown sha is an empty list with HTTP 200;
  ``total_count`` counts every match, not the page). ``per_page`` defaults to 30 and is capped at
  100 (``0`` and non-numbers give the default); ``page`` counts from 1. Unknown query keys are
  ignored, as GitHub does. A workflow file that does not exist is a 404.
* ``GET .../actions/runs/<id>`` is one run (404 for an unknown id); ``GET
  .../actions/runs/<id>/jobs`` returns ``{total_count, jobs}`` in order. A job has ``id``,
  ``run_id``, ``workflow_name``, ``head_branch``, ``head_sha``, ``name``, ``status``,
  ``conclusion``, ``created_at``, ``started_at``, ``completed_at``, ``steps``, ``labels``,
  ``runner_id``, ``runner_name`` and urls. A job cancelled
  while it waited reads ``completed`` / ``cancelled`` with no steps, ``runner_id`` 0 and
  ``runner_name`` "" (observed on a real run cancelled during approval).
* ``POST .../actions/runs/<id>/cancel`` answers 202 ``{}`` while the run is not completed and 409
  once it is. UNVERIFIED (a real cancel was never sent): the 409 text, and that GitHub cancels
  asynchronously; the fake cancels at once (the run and every job not completed become
  ``completed`` / ``cancelled``). A token without ``actions: write`` (state key ``actions_write``
  false) is refused with 403 ``Resource not accessible by integration``.
* ``gh workflow run <file> --repo <r> [--ref <branch>] [-f key=value]`` prints nothing on a
  non-terminal stdout (UNVERIFIED for gh 2.x releases after 2.46) and creates a run with status
  ``queued``, event ``workflow_dispatch``, ``head_branch`` the ref, ``head_sha`` the ref's tip
  (state ``branches``) and the token's actor (state ``token_actor``, ``github-actions[bot]`` by
  default; a run's ``actor`` and ``triggering_actor`` are the same for a first attempt). Refused
  as gh prints it (text UNVERIFIED): an unknown workflow (``could not find any workflow named``), no
  ``workflow_dispatch`` trigger, an unknown ref, an input the workflow does not declare, and a
  token without ``actions_write`` (HTTP 403). The state key ``dispatch_no_run`` makes the dispatch
  succeed without creating a run; ``dispatch_lag`` hides a created run from the first N run reads
  (GitHub creates the run a moment after the 204). A new run has no jobs yet.
* ``status`` values used are ``queued``, ``in_progress``, ``waiting``, ``pending`` and
  ``completed`` for runs (``waiting``: the run waits for an environment approval; ``pending``: it
  queues behind a concurrency group) and the same for jobs. UNVERIFIED: the exact fields of a job
  that waits for an approval (the fake gives it ``started_at`` equal to ``created_at``, no steps and
  ``runner_id`` ``null``); a script must read only ``name``, ``status`` and ``conclusion``.
* Omitted from a run: ``node_id``, ``check_suite_*``, ``pull_requests``, ``referenced_workflows``,
  ``head_commit``, ``previous_attempt_url``, ``logs_url``, ``artifacts_url``, ``rerun_url``,
  ``workflow_url``; from a job ``node_id``, ``check_run_url``, ``runner_group_*``. The fake has one
  attempt per run and timestamps from a fake clock (one second per call that stamps a time). The
  run list with ``--paginate`` returns every match in one response and ignores ``per_page`` and
  ``page`` (real gh prints one JSON document per page).

How it differs from real ``gh`` on purpose, or where its fidelity is unverified (scripts and tests
must not depend on any of these):

* ``--jq`` runs the system ``jq`` with ``-S`` (object keys sorted, as gh's gojq does). gojq's regex
  engine and some builtins differ from jq's, and the fake's jq is the more lenient of the two: a
  filter that passes here can still fail in real gh.
* ``--paginate`` returns one page and ignores ``per_page``. Like real gh (verified in gh source,
  pkg/cmd/api/api.go) it is refused client-side, exit 1, before any request: with an explicit
  non-GET ``--method`` ("the `--paginate` option is not supported for non-GET requests"), then with
  ``--input`` even beside an explicit GET ("the `--paginate` option is not supported with
  `--input`"); both checks come before the implicit POST of ``--input`` is applied.
* A policy POST whose pattern already exists answers ``gh: HTTP 303`` (exit 1). GitHub's OpenAPI
  description lists 303 for it; how gh prints a 303 (text, exit code) is UNVERIFIED.
* ``render_environment`` lists the ``branch_policy`` protection rule only for custom policies;
  whether GitHub also lists it when only ``protected_branches`` is true is UNVERIFIED.
* A Team reviewer gets a ``login`` in the fake's GET; real GitHub shows a team's ``slug``/``name``
  instead. Team reviewers are out of scope: their ids are not looked up.
* ``reviewers`` in a PUT: at most 6; a ``User`` id must be a stored user whose type is ``User``
  (else 422, an Organization id included). Names of environments and users match
  case-insensitively and the stored spelling is returned.
* ``gh secret set`` / ``gh variable set``: names are stored uppercase and a ``GITHUB_`` prefix
  (any case) is refused with 422; a secret read from stdin loses trailing CR/LF characters; a
  logged-out gh exits 4. The 404 text of ``secret set`` is ``gh: Not Found (HTTP 404)`` (the plan's
  wording); real gh prints ``failed to fetch public key: HTTP 404: Not Found`` (verified in gh
  source, set.go), so a script must never match on that text. ``--body ""`` falls back to stdin in
  real gh (verified in gh source: ``getBody`` tests ``opts.Body != ""`` in secret set and variable
  set) and is not modelled: the fake treats any ``--body`` to ``secret set`` as a test bug, and
  accepts only ``--body`` for ``variable set``. Real ``variable set`` also trims trailing CR/LF
  from a value read on stdin; that is not modelled, so a ``--body`` value is stored untrimmed,
  which is right for ``--body``.
* A secret passed on argv to ``secret set`` is a test bug (exit 252), and its value is redacted in
  the log and in the error text, so it never reaches a log file or a terminal.
* The repository (``owner/repo``) matches case-insensitively, as on GitHub, and the stored
  spelling is returned.

Anything the fake does not model exits 252 with ``fake gh: unsupported call: ...``: that is a test
bug, never a silent success. There is deliberately no ``gh auth status``.

Stand-ins only. The module defines no pytest fixture (importers define their own).
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = "example-owner/CSAV2"
REPO_ID = 42
USERS = {"reviewer-a": 1001, "reviewer-b": 1002}
# The actor of a run made with a workflow's own token (these two are GitHub's public values).
BOT = {"login": "github-actions[bot]", "id": 41898282, "type": "Bot"}
# The tip of main that a dispatch with --ref main runs on (a synthetic sha).
TIP = "0123456789abcdef0123456789abcdef01234567"
# An `override` value that removes the key from a rendered run instead of replacing it.
MISSING_FIELD = "<missing field>"

FAKE_GH = r'''#!__PYTHON__
"""A stateful fake of the GitHub CLI, for the deploy scripts' tests only."""
import datetime
import json
import os
import re
import subprocess
import sys
from urllib.parse import parse_qs

MISSING = "__MISSING__"
STATE = os.environ["FAKE_GH_STATE"]
LOG = os.environ["FAKE_GH_LOG"]
DOCS = "https://docs.github.com/rest"
argv = sys.argv[1:]
stdin_length = None


def load():
    with open(STATE) as handle:
        return json.load(handle)


def save(state):
    with open(STATE, "w") as handle:
        json.dump(state, handle)


def redact(args):
    # A value given to `gh secret set` on argv (--body VALUE, -b VALUE, --body=VALUE, -b=VALUE,
    # -bVALUE) must reach neither the log nor stderr: it is replaced before anything is written.
    if args[:2] != ["secret", "set"]:
        return list(args)
    shown = []
    hide_next = False
    for arg in args:
        if hide_next:
            shown.append("REDACTED")
            hide_next = False
        elif arg in ("--body", "-b"):
            shown.append(arg)
            hide_next = True
        elif arg.startswith("--body="):
            shown.append("--body=REDACTED")
        elif arg.startswith("-b") and not arg.startswith("--"):
            shown.append("-b=REDACTED" if arg.startswith("-b=") else "-bREDACTED")
        else:
            shown.append(arg)
    return shown


def finish(code):
    # The one exit. It logs argv (a secret on argv redacted) and the byte length of stdin, never
    # stdin content.
    with open(LOG, "a") as handle:
        handle.write(json.dumps({"argv": redact(argv), "stdin": stdin_length}) + "\n")
    sys.stdout.flush()
    sys.stderr.flush()
    sys.exit(code)


def bug(text):
    sys.stderr.write(f"fake gh: {text}\n")
    finish(252)


def unsupported():
    bug("unsupported call: " + " ".join(redact(argv)))


def http_error(status, message):
    print(json.dumps({"message": message, "documentation_url": DOCS, "status": str(status)}))
    sys.stderr.write(f"gh: {message} (HTTP {status})\n")
    finish(1)


def not_found():
    http_error(404, "Not Found")


def invalid():
    http_error(422, "Validation Failed")


def read_stdin():
    global stdin_length
    data = sys.stdin.buffer.read()
    stdin_length = len(data)
    return data.decode()


def emit(payload, jq):
    text = json.dumps(payload)
    if jq is not None:
        try:
            done = subprocess.run(
                ["jq", "-S", "-c", "-r", jq], input=text, capture_output=True, text=True
            )
        except FileNotFoundError:
            bug("jq is not installed")
        sys.stderr.write(done.stderr)
        sys.stdout.write(done.stdout)
        finish(1 if done.returncode else 0)
    print(text)
    finish(0)


def split_option(args, index, names):
    # Returns the value of an option given as "--name value" or "--name=value".
    arg = args[index]
    for name in names:
        if arg == name:
            if index + 1 >= len(args):
                bug(f"{name} needs a value")
            return args[index + 1], 2
        if name.startswith("--") and arg.startswith(name + "="):
            return arg[len(name) + 1:], 1
    return None, 0


def parse_api(args):
    opts = {"method": None, "input": False, "jq": None, "paginate": False}
    path = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--paginate":
            opts["paginate"] = True
            i += 1
            continue
        value, used = split_option(args, i, ["--method", "-X"])
        if used:
            opts["method"] = value.upper()
            i += used
            continue
        value, used = split_option(args, i, ["--jq", "-q"])
        if used:
            opts["jq"] = value
            i += used
            continue
        value, used = split_option(args, i, ["--input"])
        if used:
            if value != "-":
                bug("--input must be - (stdin)")
            opts["input"] = True
            i += used
            continue
        if arg.startswith("-"):
            unsupported()
        if path is not None:
            unsupported()
        path = arg
        i += 1
    if path is None:
        unsupported()
    # Real gh checks these in this order, both before it applies the implicit POST of --input.
    if opts["paginate"] and opts["method"] not in (None, "GET"):
        sys.stderr.write("the `--paginate` option is not supported for non-GET requests\n")
        finish(1)
    if opts["paginate"] and opts["input"]:
        sys.stderr.write("the `--paginate` option is not supported with `--input`\n")
        finish(1)
    if opts["method"] is None:
        opts["method"] = "POST" if opts["input"] else "GET"
    if opts["method"] not in ("GET", "POST", "PUT", "DELETE"):
        unsupported()
    return opts, path


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def find_name(names, wanted):
    # GitHub matches environment names and logins case-insensitively; return the stored spelling.
    for name in names:
        if name.lower() == wanted.lower():
            return name
    return None


def user_of(state, user_id):
    for login, entry in state["users"].items():
        user = entry if isinstance(entry, dict) else {"id": entry}
        if user["id"] == user_id:
            return {"login": login, "id": user_id, "type": user.get("type", "User")}
    return None


def login_of(state, user_id):
    user = user_of(state, user_id)
    return user["login"] if user else f"user-{user_id}"


def render_environment(state, name):
    env = state["environments"][name]
    rules = []
    if env["reviewers"]:
        reviewers = [
            {
                "type": item["type"],
                "reviewer": {
                    "id": item["id"],
                    "login": login_of(state, item["id"]),
                    "type": item["type"],
                },
            }
            for item in env["reviewers"]
        ]
        rules.append(
            {
                "type": "required_reviewers",
                "prevent_self_review": env["prevent_self_review"],
                "reviewers": reviewers,
            }
        )
    if env["wait_timer"]:
        rules.append({"type": "wait_timer", "wait_timer": env["wait_timer"]})
    if env["custom_branch_policies"]:
        rules.append({"type": "branch_policy"})
    policy = None
    if env["custom_branch_policies"] or env["protected_branches"]:
        policy = {
            "protected_branches": env["protected_branches"],
            "custom_branch_policies": env["custom_branch_policies"],
        }
    return {
        "id": env["id"],
        "name": name,
        "can_admins_bypass": env["can_admins_bypass"],
        "protection_rules": rules,
        "deployment_branch_policy": policy,
    }


def new_environment(state):
    env_id = state["next_id"]
    state["next_id"] = env_id + 1
    return {
        "id": env_id,
        "can_admins_bypass": True,
        "wait_timer": 0,
        "prevent_self_review": False,
        "reviewers": [],
        "custom_branch_policies": False,
        "protected_branches": False,
        "policies": [],
        "secrets": {},
    }


def validate_put(state, body, ignored):
    if not isinstance(body, dict):
        invalid()

    def given(key):
        return key in body and key not in ignored

    if given("reviewers"):
        reviewers = body["reviewers"]
        if reviewers is not None:
            if not isinstance(reviewers, list) or len(reviewers) > 6:
                invalid()
            for item in reviewers:
                ok = isinstance(item, dict) and item.get("type") in ("User", "Team")
                if not (ok and is_int(item.get("id"))):
                    invalid()
                if item["type"] == "User":
                    # A User reviewer must be an existing account of type User. Team ids are not
                    # looked up (out of scope).
                    account = user_of(state, item["id"])
                    if account is None or account["type"] != "User":
                        invalid()
    if given("wait_timer"):
        value = body["wait_timer"]
        if not (is_int(value) and 0 <= value <= 43200):
            invalid()
    for key in ("prevent_self_review", "can_admins_bypass"):
        if given(key) and not isinstance(body[key], bool):
            invalid()
    if given("deployment_branch_policy"):
        policy = body["deployment_branch_policy"]
        if policy is not None:
            if not isinstance(policy, dict):
                invalid()
            protected = policy.get("protected_branches")
            custom = policy.get("custom_branch_policies")
            if not (isinstance(protected, bool) and isinstance(custom, bool)):
                invalid()
            if protected == custom:
                invalid()


def apply_put(state, env, body):
    ignored = set(state.get("put_ignores", []))
    replace = state.get("put_semantics", "merge") == "replace"

    def given(key):
        return key in body and key not in ignored

    if given("reviewers"):
        env["reviewers"] = [
            {"type": item["type"], "id": item["id"]} for item in (body["reviewers"] or [])
        ]
    elif replace:
        env["reviewers"] = []
    if given("wait_timer"):
        env["wait_timer"] = body["wait_timer"]
    elif replace:
        env["wait_timer"] = 0
    if given("prevent_self_review"):
        env["prevent_self_review"] = body["prevent_self_review"]
    elif replace:
        env["prevent_self_review"] = False
    if given("can_admins_bypass"):
        env["can_admins_bypass"] = body["can_admins_bypass"]
    elif replace:
        env["can_admins_bypass"] = True
    if given("deployment_branch_policy") or replace:
        policy = body["deployment_branch_policy"] if given("deployment_branch_policy") else None
        custom = bool(policy and policy["custom_branch_policies"])
        env["protected_branches"] = bool(policy and policy["protected_branches"])
        if not custom:
            env["policies"] = []
        env["custom_branch_policies"] = custom


def environment_route(state, name, tail, method, opts):
    envs = state["environments"]
    existing = find_name(envs, name)
    if tail == "" and method == "GET":
        if existing is None:
            not_found()
        emit(render_environment(state, existing), opts["jq"])
    if tail == "" and method == "PUT":
        try:
            body = json.loads(read_stdin()) if opts["input"] else {}
        except ValueError:
            http_error(400, "Problems parsing JSON")
        validate_put(state, body, set(state.get("put_ignores", [])))
        if existing is None:
            existing = name
            envs[name] = new_environment(state)
        apply_put(state, envs[existing], body)
        state.setdefault("api_bodies", []).append(
            {"method": "PUT", "path": existing, "body": body}
        )
        save(state)
        emit(render_environment(state, existing), opts["jq"])
    if tail == "/secrets" and method == "GET":
        if existing is None:
            not_found()
        secrets = [{"name": key} for key in sorted(envs[existing]["secrets"])]
        emit({"total_count": len(secrets), "secrets": secrets}, opts["jq"])
    if tail.startswith("/deployment-branch-policies"):
        policies_route(state, existing, tail[len("/deployment-branch-policies"):], method, opts)
    unsupported()


def policies_route(state, name, tail, method, opts):
    env = state["environments"].get(name) if name is not None else None
    # Policies exist only with custom policies on (GitHub answers 404 otherwise).
    if env is None or not env["custom_branch_policies"]:
        not_found()
    policies = env["policies"]
    if tail == "" and method == "GET":
        listed = [{"id": p["id"], "name": p["name"], "type": p["type"]} for p in policies]
        emit({"total_count": len(listed), "branch_policies": listed}, opts["jq"])
    if tail == "" and method == "POST":
        try:
            body = json.loads(read_stdin()) if opts["input"] else {}
        except ValueError:
            http_error(400, "Problems parsing JSON")
        if not isinstance(body, dict):
            invalid()
        pattern = body.get("name")
        kind = body.get("type", "branch")
        if not isinstance(pattern, str) or not pattern or kind not in ("branch", "tag"):
            invalid()
        if any(p["name"] == pattern and p["type"] == kind for p in policies):
            # GitHub's OpenAPI description lists 303 for a pattern that already exists.
            sys.stderr.write("gh: HTTP 303\n")
            finish(1)
        policy_id = state["next_id"]
        state["next_id"] = policy_id + 1
        policies.append({"id": policy_id, "name": pattern, "type": kind})
        state.setdefault("api_bodies", []).append(
            {"method": "POST", "path": name + "/policies", "body": body}
        )
        save(state)
        emit({"id": policy_id, "name": pattern, "type": kind}, opts["jq"])
    if tail.startswith("/") and tail[1:].isdigit() and method == "DELETE":
        found = [p for p in policies if p["id"] == int(tail[1:])]
        if not found:
            not_found()
        policies.remove(found[0])
        save(state)
        finish(0)
    unsupported()

def stamp(state):
    # A fake clock: one second per call that stamps a time, so the order of events is stable.
    state["tick"] = state.get("tick", 0) + 1
    base = datetime.datetime(2026, 11, 18, 8, 0, 0, tzinfo=datetime.timezone.utc)
    return (base + datetime.timedelta(seconds=state["tick"])).strftime("%Y-%m-%dT%H:%M:%SZ")


def workflow_name(file):
    return file.rsplit(".", 1)[0]


def render_run(state, run):
    workflow = state["workflows"][run["workflow"]]
    repo = state["repo"]["full_name"]
    api = f"https://api.github.com/repos/{repo}/actions/runs/{run['id']}"
    shown_repo = {"id": state["repo"]["id"], "full_name": repo}
    out = {
        "id": run["id"],
        "name": workflow_name(run["workflow"]),
        "head_branch": run["head_branch"],
        "head_sha": run["head_sha"],
        "path": ".github/workflows/" + run["workflow"],
        "display_title": workflow_name(run["workflow"]),
        "run_number": run["run_number"],
        "event": run["event"],
        "status": run["status"],
        "conclusion": run["conclusion"],
        "workflow_id": workflow["id"],
        "url": api,
        "html_url": f"https://github.com/{repo}/actions/runs/{run['id']}",
        "created_at": run["created_at"],
        "updated_at": run["updated_at"],
        "actor": run["actor"],
        "run_attempt": 1,
        "run_started_at": run["created_at"],
        "triggering_actor": run["triggering_actor"],
        "jobs_url": api + "/jobs",
        "cancel_url": api + "/cancel",
        "repository": shown_repo,
        "head_repository": shown_repo,
    }
    for key, value in run.get("override", {}).items():
        if value == MISSING:
            out.pop(key, None)
        else:
            out[key] = value
    return out


def render_job(state, run, job):
    repo = state["repo"]["full_name"]
    done = job["status"] == "completed"
    cancelled_unstarted = done and job["conclusion"] == "cancelled" and not job["steps"]
    started = job["started_at"] is not None
    return {
        "id": job["id"],
        "run_id": run["id"],
        "workflow_name": workflow_name(run["workflow"]),
        "head_branch": run["head_branch"],
        "run_url": f"https://api.github.com/repos/{repo}/actions/runs/{run['id']}",
        "run_attempt": 1,
        "head_sha": run["head_sha"],
        "url": f"https://api.github.com/repos/{repo}/actions/jobs/{job['id']}",
        "html_url": f"https://github.com/{repo}/actions/runs/{run['id']}/job/{job['id']}",
        "status": job["status"],
        "conclusion": job["conclusion"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "completed_at": job["completed_at"],
        "name": job["name"],
        "steps": job["steps"],
        "labels": ["ubuntu-24.04"],
        "runner_id": 0 if cancelled_unstarted else (1000000001 if started else None),
        "runner_name": "" if cancelled_unstarted else ("GitHub Actions" if started else None),
    }


def page_of(state, query, items, opts):
    # per_page defaults to 30 and is capped at 100 (0 and non-numbers give the default); page counts
    # from 1. With --paginate every item comes back in one response.
    if opts["paginate"]:
        return items

    def number(key, default, limit):
        text = query.get(key, "")
        if text.isdigit() and int(text) >= 1:
            return min(int(text), limit)
        return default

    size = number("per_page", 30, 100)
    page = number("page", 1, 10**9)
    return items[(page - 1) * size : page * size]


def visible_runs(state):
    # A dispatched run shows up only after `dispatch_lag` reads (GitHub creates it a moment later).
    state["list_calls"] = state.get("list_calls", 0) + 1
    save(state)
    return [r for r in state["runs"] if r.get("hidden_until", 0) < state["list_calls"]]


def find_run(state, text):
    if not text.isdigit():
        not_found()
    found = [r for r in visible_runs(state) if r["id"] == int(text)]
    if not found:
        not_found()
    return found[0]


def runs_route(state, rest, query, method, opts):
    # rest: what follows repos/<owner>/<repo>/actions/ .
    if len(rest) == 3 and rest[0] == "workflows" and rest[2] == "runs" and method == "GET":
        files = state["workflows"]
        file = next((f for f in files if f == rest[1] or str(files[f]["id"]) == rest[1]), None)
        if file is None:
            not_found()
        runs = [r for r in visible_runs(state) if r["workflow"] == file]
        for key, field in (("event", "event"), ("branch", "head_branch"), ("head_sha", "head_sha")):
            if query.get(key):
                runs = [r for r in runs if r[field] == query[key]]
        if query.get("status"):
            runs = [r for r in runs if query["status"] in (r["status"], r["conclusion"])]
        if query.get("actor"):
            runs = [r for r in runs if r["actor"]["login"].lower() == query["actor"].lower()]
        runs.sort(key=lambda r: (r["created_at"], r["run_number"]), reverse=True)
        shown = [render_run(state, r) for r in page_of(state, query, runs, opts)]
        emit({"total_count": len(runs), "workflow_runs": shown}, opts["jq"])
    if len(rest) >= 2 and rest[0] == "runs":
        run = find_run(state, rest[1])
        if len(rest) == 2 and method == "GET":
            emit(render_run(state, run), opts["jq"])
        if rest[2:] == ["jobs"] and method == "GET":
            jobs = [render_job(state, run, j) for j in run["jobs"]]
            emit({"total_count": len(jobs), "jobs": page_of(state, query, jobs, opts)}, opts["jq"])
        if rest[2:] == ["cancel"] and method == "POST":
            if not state.get("actions_write", True):
                http_error(403, "Resource not accessible by integration")
            if run["status"] == "completed":
                http_error(409, "Cannot cancel a workflow run that is completed")
            now = stamp(state)
            for job in run["jobs"]:
                if job["status"] != "completed":
                    job.update(status="completed", conclusion="cancelled", steps=[])
                    job["completed_at"] = now
            run.update(status="completed", conclusion="cancelled", updated_at=now)
            save(state)
            print("{}")
            finish(0)
    unsupported()


def api_command(args):
    opts, raw_path = parse_api(args)
    method = opts["method"]
    path, _, query = raw_path.lstrip("/").partition("?")
    params = {key: values[0] for key, values in parse_qs(query).items()}
    state = load()
    require_login(state)
    parts = path.split("/")
    if parts[0] == "users" and len(parts) == 2 and method == "GET":
        login = find_name(state["users"], parts[1])
        if login is None:
            not_found()
        entry = state["users"][login]
        user = entry if isinstance(entry, dict) else {"id": entry}
        emit({"login": login, "id": user["id"], "type": user.get("type", "User")}, opts["jq"])
    if parts[0] == "repos" and len(parts) >= 3:
        repo = state["repo"]
        if f"{parts[1]}/{parts[2]}".lower() != repo["full_name"].lower():
            not_found()
        rest = parts[3:]
        if not rest and method == "GET":
            emit(
                {
                    "id": repo["id"],
                    "full_name": repo["full_name"],
                    "permissions": {"admin": repo["admin"]},
                },
                opts["jq"],
            )
        if rest == ["actions", "variables"] and method == "GET":
            listed = [{"name": k, "value": v} for k, v in sorted(state["variables"].items())]
            emit({"total_count": len(listed), "variables": listed}, opts["jq"])
        if len(rest) >= 2 and rest[0] == "environments":
            environment_route(state, rest[1], "".join("/" + p for p in rest[2:]), method, opts)
        if len(rest) >= 3 and rest[0] == "actions" and rest[1] in ("workflows", "runs"):
            runs_route(state, rest[1:], params, method, opts)
    unsupported()


def option_values(args, names):
    # Splits "gh secret set"/"gh variable set" arguments into positionals and known options.
    values = {}
    positional = []
    i = 0
    while i < len(args):
        arg = args[i]
        for key, option_names in names.items():
            value, used = split_option(args, i, option_names)
            if used:
                values[key] = value
                i += used
                break
        else:
            if arg.startswith("-"):
                unsupported()
            positional.append(arg)
            i += 1
    return values, positional


def valid_name(name):
    if name.upper().startswith("GITHUB_"):
        return False
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is not None


def require_login(state):
    if state.get("logged_out"):
        sys.stderr.write("gh: To get started with GitHub CLI, please run:  gh auth login\n")
        finish(4)


def secret_set(args):
    names = {"env": ["--env", "-e"], "repo": ["--repo", "-R"], "body": ["--body", "-b"]}
    values, positional = option_values(args, names)
    if "body" in values:
        bug("secret values must reach gh on stdin, never --body")
    if "repo" not in values or "env" not in values or len(positional) != 1:
        unsupported()
    value = read_stdin().rstrip("\r\n")
    state = load()
    require_login(state)
    env_name = find_name(state["environments"], values["env"])
    if values["repo"].lower() != state["repo"]["full_name"].lower() or env_name is None:
        sys.stderr.write("gh: Not Found (HTTP 404)\n")
        finish(1)
    if not valid_name(positional[0]):
        invalid()
    state["environments"][env_name]["secrets"][positional[0].upper()] = value
    save(state)
    finish(0)


def variable_set(args):
    names = {"repo": ["--repo", "-R"], "body": ["--body", "-b"]}
    values, positional = option_values(args, names)
    if "repo" not in values or "body" not in values or len(positional) != 1:
        unsupported()
    state = load()
    require_login(state)
    if values["repo"].lower() != state["repo"]["full_name"].lower():
        sys.stderr.write("gh: Not Found (HTTP 404)\n")
        finish(1)
    if not valid_name(positional[0]):
        invalid()
    state["variables"][positional[0].upper()] = values["body"]
    save(state)
    finish(0)


def workflow_run(args):
    # gh workflow run <file> --repo <r> [--ref <branch>] [-f key=value ...]
    repo = ref = None
    selector = None
    fields = []
    i = 0
    while i < len(args):
        for name, target in (("repo", ["--repo", "-R"]), ("ref", ["--ref", "-r"])):
            value, used = split_option(args, i, target)
            if used:
                if name == "repo":
                    repo = value
                else:
                    ref = value
                i += used
                break
        else:
            value, used = split_option(args, i, ["--raw-field", "-f"])
            if used:
                if "=" not in value:
                    bug("-f needs key=value")
                fields.append(value.split("=", 1))
                i += used
            elif args[i].startswith("-") or selector is not None:
                unsupported()
            else:
                selector = args[i]
                i += 1
    if repo is None or selector is None:
        unsupported()
    state = load()
    require_login(state)
    if repo.lower() != state["repo"]["full_name"].lower():
        http_error(404, "Not Found")
    files = state["workflows"]
    file = next(
        (f for f in files if selector in (f, workflow_name(f), str(files[f]["id"]))), None
    )
    if file is None:
        sys.stderr.write(f"could not find any workflow named {selector}\n")
        finish(1)
    workflow = files[file]
    ref = ref if ref is not None else "main"
    where = (
        f"https://api.github.com/repos/{state['repo']['full_name']}/actions/workflows/"
        f"{workflow['id']}/dispatches"
    )

    def refuse(status, message):
        sys.stderr.write(
            f"could not create workflow dispatch event: HTTP {status}: {message} ({where})\n"
        )
        finish(1)

    if not state.get("actions_write", True):
        refuse(403, "Resource not accessible by integration")
    if not workflow["dispatch"]:
        refuse(422, "Workflow does not have 'workflow_dispatch' trigger")
    if ref not in state["branches"]:
        refuse(422, f"No ref found for: {ref}")
    unknown = sorted(key for key, _ in fields if key not in workflow["inputs"])
    if unknown:
        refuse(422, "Unexpected inputs provided: " + json.dumps(unknown))
    if not state.get("dispatch_no_run"):
        run_id = state["next_run_id"]
        state["next_run_id"] = run_id + 1
        now = stamp(state)
        state["runs"].append(
            {
                "id": run_id,
                "workflow": file,
                "run_number": 1 + sum(1 for r in state["runs"] if r["workflow"] == file),
                "event": "workflow_dispatch",
                "status": "queued",
                "conclusion": None,
                "head_branch": ref,
                "head_sha": state["branches"][ref],
                "actor": state["token_actor"],
                "triggering_actor": state["token_actor"],
                "created_at": now,
                "updated_at": now,
                "jobs": [],
                "inputs": dict(fields),
                "hidden_until": state.get("list_calls", 0) + state.get("dispatch_lag", 0),
            }
        )
    save(state)
    finish(0)


if argv[:1] == ["api"]:
    api_command(argv[1:])
elif argv[:2] == ["secret", "set"]:
    secret_set(argv[2:])
elif argv[:2] == ["variable", "set"]:
    variable_set(argv[2:])
elif argv[:2] == ["workflow", "run"]:
    workflow_run(argv[2:])
else:
    unsupported()
'''.replace("__PYTHON__", sys.executable).replace("__MISSING__", MISSING_FIELD)

# `gh` subcommands that change something: (group, verb).
WRITE_VERBS = {
    "secret": {"set", "delete"},
    "variable": {"set", "delete"},
    "workflow": {"run", "enable", "disable"},
    "run": {"cancel", "rerun", "delete"},
}


FIELD_FLAGS = ("-f", "-F", "--field", "--raw-field")


def is_field_flag(arg: str) -> bool:
    """A ``gh api`` field flag: ``-f``/``-F``/``--field``/``--raw-field`` alone, ``--field=...``,
    ``--raw-field=...``, or a short flag with its value attached (``-f=a=b``, ``-fa=b``)."""
    if arg in FIELD_FLAGS or arg.startswith(("--field=", "--raw-field=")):
        return True
    return arg[:2] in ("-f", "-F") and not arg.startswith("--")


def api_method(argv: list[str]) -> str:
    """The HTTP method a ``gh api`` call uses: explicit (``--method``/``-X``, either spelling), else
    POST with ``--input`` (``--input -`` or ``--input=-``) or with a field flag (``-f``, ``-F``,
    ``--field``, ``--raw-field``, in any spelling gh accepts), else GET. The fake does not model
    fields (such a call exits 252), but it is still classified as the write it would be."""
    method: str | None = None
    has_input = False
    for index, arg in enumerate(argv):
        if arg in ("--method", "-X") and index + 1 < len(argv):
            method = argv[index + 1].upper()
        elif arg.startswith("--method="):
            method = arg.split("=", 1)[1].upper()
        elif arg == "--input" or arg.startswith("--input="):
            has_input = True
        elif is_field_flag(arg):
            has_input = True
    if method is not None:
        return method
    return "POST" if has_input else "GET"


class Gh:
    """The fake ``gh`` plus a way to run a script against it."""

    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.tmp = tmp_path / "scratch"
        self.tmp.mkdir()
        self.state_path = tmp_path / "state.json"
        self.log_path = tmp_path / "log.jsonl"
        fake = self.bin / "gh"
        fake.write_text(FAKE_GH)
        fake.chmod(0o755)
        self.log_path.write_text("")
        self.save(
            {
                "repo": {"id": REPO_ID, "full_name": REPO, "admin": True},
                "users": dict(USERS),
                "environments": {},
                "variables": {},
                "workflows": {
                    "deploy-production.yml": {"id": 4242, "dispatch": True, "inputs": ["sha"]}
                },
                "branches": {"main": TIP},
                "token_actor": dict(BOT),
                "actions_write": True,
                "dispatch_no_run": False,
                "dispatch_lag": 0,
                "runs": [],
                "next_run_id": 7000000001,
                "next_id": 1,
                "put_semantics": "merge",
                "put_ignores": [],
                "logged_out": False,
            }
        )

    def state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text())

    def save(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state))

    def api_bodies(self) -> list[dict[str, Any]]:
        """The bodies of the accepted PUTs and policy POSTs, in order: ``{"method", "path",
        "body"}``. A refused (4xx) request is never recorded."""
        return list(self.state().get("api_bodies", []))

    def calls(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    def commands(self) -> list[str]:
        """One label per call: ``api <METHOD> <path>`` for ``gh api``, else the first two words."""
        labels = []
        for call in self.calls():
            argv = call["argv"]
            if argv[:1] == ["api"]:
                path = [arg for arg in argv[1:] if not arg.startswith("-")]
                # The path is the one positional that is not the value of an option.
                values = {
                    argv[i + 1]
                    for i, arg in enumerate(argv)
                    if arg in ("--method", "-X", "--jq", "-q", *FIELD_FLAGS) and i + 1 < len(argv)
                }
                path = [arg for arg in path if arg not in values]
                labels.append(f"api {api_method(argv)} {path[0] if path else ''}")
            else:
                labels.append(" ".join(argv[:2]))
        return labels

    def set_state(self, **values: Any) -> None:
        """Set top-level state keys (``actions_write``, ``dispatch_no_run``, ``dispatch_lag``,
        ``token_actor``, ``logged_out``, ``put_semantics``, ...)."""
        state = self.state()
        state.update(values)
        self.save(state)

    def person(self, login: str) -> dict[str, Any]:
        """The ``{login, id, type}`` of an account: the bot, a stored user, or a synthetic one (the
        id of an unknown login is derived from the name; only the login matters)."""
        if login == BOT["login"]:
            return dict(BOT)
        users = self.state()["users"]
        entry = users.get(login)
        if entry is not None:
            return {
                "login": login,
                "id": entry["id"] if isinstance(entry, dict) else entry,
                "type": "User",
            }
        return {"login": login, "id": 9000 + sum(map(ord, login)) % 1000, "type": "User"}

    def add_run(
        self,
        *,
        workflow: str = "deploy-production.yml",
        status: str = "completed",
        conclusion: str | None = "success",
        event: str = "workflow_dispatch",
        head_sha: str = TIP,
        head_branch: str = "main",
        actor: str = "reviewer-a",
        triggering_actor: str | None = None,
        jobs: list[tuple[str, str, str | None]] | None = None,
        override: dict[str, Any] | None = None,
    ) -> int:
        """Plant a run and return its id. ``jobs`` is ``(name, status, conclusion)`` triples in
        order. ``override`` replaces keys of the rendered run (``{"triggering_actor": None}`` for a
        deleted account) or, with the value ``MISSING_FIELD``, removes them. The conclusion must be
        ``None`` unless the status is ``completed``, as on GitHub."""
        assert (status == "completed") == (conclusion is not None), (status, conclusion)
        state = self.state()
        assert workflow in state["workflows"], workflow
        run_id = state["next_run_id"]
        state["next_run_id"] = run_id + 1
        tick = state.get("tick", 0) + 1
        state["tick"] = tick
        created = (
            datetime.datetime(2026, 11, 18, 8, 0, 0, tzinfo=datetime.UTC)
            + datetime.timedelta(seconds=tick)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        stored_jobs = []
        for name, job_status, job_conclusion in jobs or ():
            assert (job_status == "completed") == (job_conclusion is not None), (name, job_status)
            job_id = state["next_run_id"]
            state["next_run_id"] = job_id + 1
            finished = job_status == "completed"
            stored_jobs.append(
                {
                    "id": job_id,
                    "name": name,
                    "status": job_status,
                    "conclusion": job_conclusion,
                    "created_at": created,
                    "started_at": created,
                    "completed_at": created if finished else None,
                    "steps": []
                    if job_status in ("waiting", "queued", "pending")
                    else [
                        {
                            "name": "Set up job",
                            "status": job_status,
                            "conclusion": job_conclusion,
                            "number": 1,
                        }
                    ],
                }
            )
        state["runs"].append(
            {
                "id": run_id,
                "workflow": workflow,
                "run_number": 1 + sum(1 for r in state["runs"] if r["workflow"] == workflow),
                "event": event,
                "status": status,
                "conclusion": conclusion,
                "head_branch": head_branch,
                "head_sha": head_sha,
                "actor": self.person(actor),
                "triggering_actor": self.person(triggering_actor or actor),
                "created_at": created,
                "updated_at": created,
                "jobs": stored_jobs,
                "override": override or {},
                "hidden_until": 0,
            }
        )
        self.save(state)
        return run_id

    def reset_log(self) -> None:
        self.log_path.write_text("")

    def writes(self) -> list[dict[str, Any]]:
        """Calls that are not reads: an ``api`` call with a method other than GET, a secret or
        variable set, a workflow run, a run cancel."""
        found = []
        for call in self.calls():
            argv = call["argv"]
            if argv[:1] == ["api"]:
                if api_method(argv) != "GET":
                    found.append(call)
            elif len(argv) >= 2 and argv[1] in WRITE_VERBS.get(argv[0], set()):
                found.append(call)
        return found

    def add_environment(
        self,
        name: str,
        *,
        reviewers: tuple[str, ...] | list[str] = (),
        wait_timer: int = 0,
        prevent_self_review: bool = False,
        can_admins_bypass: bool = True,
        policies: tuple[str, ...] | list[str] | None = None,
        protected_branches: bool = False,
        secrets: dict[str, str] | None = None,
    ) -> None:
        """Plant an environment. ``policies=None`` means custom policies off (a real
        ``deployment_branch_policy: null``); a list, even an empty one, turns them on."""
        state = self.state()
        users = state["users"]
        ids = [
            users[login]["id"] if isinstance(users[login], dict) else users[login]
            for login in reviewers
        ]
        env_id = state["next_id"]
        state["next_id"] = env_id + 1
        listed = []
        for pattern in policies or ():
            listed.append({"id": state["next_id"], "name": pattern, "type": "branch"})
            state["next_id"] += 1
        state["environments"][name] = {
            "id": env_id,
            "can_admins_bypass": can_admins_bypass,
            "wait_timer": wait_timer,
            "prevent_self_review": prevent_self_review,
            "reviewers": [{"type": "User", "id": user_id} for user_id in ids],
            "custom_branch_policies": policies is not None,
            "protected_branches": protected_branches,
            "policies": listed,
            "secrets": dict(secrets or {}),
        }
        self.save(state)

    def environment(self, env: dict[str, str] | None = None) -> dict[str, str]:
        """The environment a script runs in: the fake first on PATH, no inherited token."""
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("GH_", "GITHUB_", "FAKE_GH_"))
        }
        environment.update(
            {
                "PATH": f"{self.bin}:{os.environ['PATH']}",
                "TMPDIR": str(self.tmp),
                "FAKE_GH_STATE": str(self.state_path),
                "FAKE_GH_LOG": str(self.log_path),
            }
        )
        environment.update(env or {})
        return environment

    def run(
        self,
        script: Path,
        *args: str,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a script with the fake ``gh`` first on ``PATH``. Without ``stdin`` the script's
        stdin is empty (never the test runner's)."""
        return subprocess.run(
            [str(script), *args],
            capture_output=True,
            text=True,
            env=self.environment(env),
            cwd=self.tmp,
            input=stdin if stdin is not None else "",
            check=False,
        )

    def call(
        self, *args: str, stdin: str | None = None, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the fake ``gh`` itself with these arguments (for tests of the fake)."""
        return self.run(self.bin / "gh", *args, env=env, stdin=stdin)
