"""A stateful fake ``gh`` (GitHub CLI) for the deploy scripts' tests.

The fake is a small Python program written into ``<tmp>/bin/gh``; that directory comes first on
``PATH`` when a script runs. It keeps a JSON state file of the repository, users, environments
(reviewers, protection settings, branch policies, secrets), repository variables and, later,
workflow runs. It logs every call (argv and the byte length of stdin, never stdin content) and
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

How it differs from real ``gh`` on purpose, or where its fidelity is unverified (scripts and tests
must not depend on any of these):

* ``--jq`` runs the system ``jq`` with ``-S`` (object keys sorted, as gh's gojq does). gojq's regex
  engine and some builtins differ from jq's, and the fake's jq is the more lenient of the two: a
  filter that passes here can still fail in real gh.
* ``--paginate`` returns one page and ignores ``per_page``. Combined with a non-GET method it is
  refused client-side with gh's message (exit 1), as real gh does.
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
  wording); real gh prints something like ``failed to fetch public key: HTTP 404: Not Found``
  (recollection, unverified), so a script must never match on that text. ``--body ""`` falls back
  to stdin in real gh (unverified) and is not modelled; ``variable set`` takes only ``--body``
  (its value is stored untrimmed, as gh does).

Anything the fake does not model exits 252 with ``fake gh: unsupported call: ...``: that is a test
bug, never a silent success. There is deliberately no ``gh auth status``.

Stand-ins only. The module defines no pytest fixture (importers define their own).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = "example-owner/CSAV2"
REPO_ID = 42
USERS = {"reviewer-a": 1001, "reviewer-b": 1002}

FAKE_GH = r'''#!__PYTHON__
"""A stateful fake of the GitHub CLI, for the deploy scripts' tests only."""
import json
import os
import re
import subprocess
import sys
from urllib.parse import parse_qs

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


def finish(code):
    # The one exit. It logs argv and the byte length of stdin, never stdin content.
    with open(LOG, "a") as handle:
        handle.write(json.dumps({"argv": argv, "stdin": stdin_length}) + "\n")
    sys.stdout.flush()
    sys.stderr.flush()
    sys.exit(code)


def bug(text):
    sys.stderr.write(f"fake gh: {text}\n")
    finish(252)


def unsupported():
    bug("unsupported call: " + " ".join(argv))


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
    if opts["method"] is None:
        opts["method"] = "POST" if opts["input"] else "GET"
    if opts["paginate"] and opts["method"] != "GET":
        sys.stderr.write("the `--paginate` option is not supported for non-GET requests\n")
        finish(1)
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
    for login, entry in state["users"].items():
        if (entry["id"] if isinstance(entry, dict) else entry) == user_id:
            return login
    return f"user-{user_id}"


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


def api_command(args):
    opts, raw_path = parse_api(args)
    method = opts["method"]
    path, _, query = raw_path.lstrip("/").partition("?")
    parse_qs(query)  # accepted and ignored (one page)
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
        if f"{parts[1]}/{parts[2]}" != repo["full_name"]:
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
    if values["repo"] != state["repo"]["full_name"] or env_name is None:
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
    if values["repo"] != state["repo"]["full_name"]:
        sys.stderr.write("gh: Not Found (HTTP 404)\n")
        finish(1)
    if not valid_name(positional[0]):
        invalid()
    state["variables"][positional[0].upper()] = values["body"]
    save(state)
    finish(0)


if argv[:1] == ["api"]:
    api_command(argv[1:])
elif argv[:2] == ["secret", "set"]:
    secret_set(argv[2:])
elif argv[:2] == ["variable", "set"]:
    variable_set(argv[2:])
else:
    unsupported()
'''.replace("__PYTHON__", sys.executable)

# `gh` subcommands that change something: (group, verb).
WRITE_VERBS = {
    "secret": {"set", "delete"},
    "variable": {"set", "delete"},
    "workflow": {"run", "enable", "disable"},
    "run": {"cancel", "rerun", "delete"},
}


def api_method(argv: list[str]) -> str:
    """The HTTP method a ``gh api`` call uses: explicit (``--method``/``-X``, either spelling), else
    POST with ``--input`` (``--input -`` or ``--input=-``), else GET. Mirrors ``parse_api``."""
    method: str | None = None
    has_input = False
    for index, arg in enumerate(argv):
        if arg in ("--method", "-X") and index + 1 < len(argv):
            method = argv[index + 1].upper()
        elif arg.startswith("--method="):
            method = arg.split("=", 1)[1].upper()
        elif arg == "--input" or arg.startswith("--input="):
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
                "runs": [],
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
                    if arg in ("--method", "-X", "--jq", "-q") and i + 1 < len(argv)
                }
                path = [arg for arg in path if arg not in values]
                labels.append(f"api {api_method(argv)} {path[0] if path else ''}")
            else:
                labels.append(" ".join(argv[:2]))
        return labels

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
