"""deploy/aws/setup-target.sh: creates or checks the AWS side of a deploy target.

The script is never run against real AWS here. A stateful fake ``aws`` (a small Python program
written into a temporary ``bin`` directory that comes first on ``PATH``) keeps a JSON state file of
providers, roles, inline policies, documents with versions, activations and nodes, logs every call
with the content of every ``file://`` argument as it was at call time, and answers like the AWS
CLI's JSON output (and its error text and exit code 254).

Stand-ins only: the region ``zz-test-1``, an invented account id and node id, and a subject
in the immutable shape with invented numbers.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.test_production_deploy_assets import _unquoted_expansions

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "aws" / "setup-target.sh"
PRODUCTION_DOC = ROOT / "deploy" / "PRODUCTION.md"

REGION = "zz-test-1"
ACCOUNT = "123456789012"
NODE = "mi-0123456789abcdef0"
SUBJECT = "repo:example-owner@1001/CSAV2@2002"
OIDC_HOST = "token.actions.githubusercontent.com"
OIDC_ARN = f"arn:aws:iam::{ACCOUNT}:oidc-provider/{OIDC_HOST}"
MARKER = "FAKE-AWS-OUTPUT-MARKER"

FAKE_AWS = r'''#!__PYTHON__
"""A stateful fake of the AWS CLI, for deploy/aws/setup-target.sh's tests only."""
import datetime
import json
import os
import re
import sys

STATE = os.environ["FAKE_AWS_STATE"]
LOG = os.environ["FAKE_AWS_LOG"]
MARKER = "__MARKER__"
argv = sys.argv[1:]


def load():
    with open(STATE) as handle:
        return json.load(handle)


def save(state):
    with open(STATE, "w") as handle:
        json.dump(state, handle)


def out(payload):
    payload = dict(payload)
    payload["Marker"] = MARKER
    print(json.dumps(payload, indent=4))
    sys.exit(0)


def fail(code, operation, text):
    sys.stderr.write(f"An error occurred ({code}) when calling the {operation} operation: {text}\n")
    sys.exit(254)


def bug(text):
    sys.stderr.write(f"fake aws: {text}\n")
    sys.exit(252)


if len(argv) < 2:
    bug("need a service and a command")
service, command = argv[0], argv[1]
opts = {}
rest = argv[2:]
i = 0
while i < len(rest):
    arg = rest[i]
    if not arg.startswith("--"):
        bug(f"unexpected argument {arg!r}")
    if i + 1 < len(rest) and not rest[i + 1].startswith("--"):
        opts[arg[2:]] = rest[i + 1]
        i += 2
    else:
        opts[arg[2:]] = True
        i += 1

files = {}
for key, value in opts.items():
    if isinstance(value, str) and value.startswith("file://"):
        with open(value[len("file://"):]) as handle:
            files[key] = handle.read()
with open(LOG, "a") as handle:
    handle.write(json.dumps({"argv": argv, "files": files}) + "\n")

if opts.get("region") != os.environ.get("FAKE_AWS_REGION"):
    bug(f"--region {opts.get('region')!r} is not the expected region")
if opts.get("output") != "json":
    bug("--output json is required")
if os.environ.get("FAKE_AWS_DENY") == f"{service} {command}":
    fail("AccessDenied", command, "User is not authorized to perform this operation.")

state = load()
account = os.environ.get("FAKE_AWS_ACCOUNT", state["account"])


def role_or_missing(operation):
    name = opts.get("role-name")
    if name not in state["roles"]:
        fail("NoSuchEntity", operation, f"The role with name {name} cannot be found.")
    return state["roles"][name]


def role_arn(name):
    return f"arn:aws:iam::{account}:role/{name}"


def document_or_missing(operation):
    name = opts.get("name")
    if name not in state["documents"]:
        fail("InvalidDocument", operation, f"Document {name} does not exist.")
    return state["documents"][name]


def normal(text):
    return json.dumps(json.loads(text), sort_keys=True)


if (service, command) == ("sts", "get-caller-identity"):
    out({"UserId": "AIDAEXAMPLE", "Account": account, "Arn": f"arn:aws:iam::{account}:user/x"})

elif (service, command) == ("iam", "list-open-id-connect-providers"):
    out({"OpenIDConnectProviderList": [{"Arn": arn} for arn in state["providers"]]})

elif (service, command) == ("iam", "create-open-id-connect-provider"):
    if opts.get("url") != "https://token.actions.githubusercontent.com":
        bug("wrong provider url")
    if opts.get("client-id-list") != "sts.amazonaws.com":
        bug("wrong client id")
    arn = f"arn:aws:iam::{account}:oidc-provider/token.actions.githubusercontent.com"
    if arn in state["providers"]:
        fail("EntityAlreadyExists", "CreateOpenIDConnectProvider", "exists")
    state["providers"].append(arn)
    save(state)
    out({"OpenIDConnectProviderArn": arn})

elif (service, command) == ("iam", "get-role"):
    role = role_or_missing("GetRole")
    out({"Role": {"RoleName": opts["role-name"], "Arn": role_arn(opts["role-name"]),
                  "AssumeRolePolicyDocument": role["trust"]}})

elif (service, command) == ("iam", "create-role"):
    name = opts.get("role-name")
    if name in state["roles"]:
        fail("EntityAlreadyExists", "CreateRole", f"Role with name {name} already exists.")
    state["roles"][name] = {"trust": json.loads(files["assume-role-policy-document"]),
                            "policies": {}, "attached": []}
    save(state)
    out({"Role": {"RoleName": name, "Arn": role_arn(name)}})

elif (service, command) == ("iam", "update-assume-role-policy"):
    role = role_or_missing("UpdateAssumeRolePolicy")
    role["trust"] = json.loads(files["policy-document"])
    save(state)
    out({})

elif (service, command) == ("iam", "put-role-policy"):
    role = role_or_missing("PutRolePolicy")
    role["policies"][opts["policy-name"]] = json.loads(files["policy-document"])
    save(state)
    out({})

elif (service, command) == ("iam", "get-role-policy"):
    role = role_or_missing("GetRolePolicy")
    name = opts.get("policy-name")
    if name not in role["policies"]:
        fail("NoSuchEntity", "GetRolePolicy", f"The role policy with name {name} cannot be found.")
    document = role["policies"][name]
    out({"RoleName": opts["role-name"], "PolicyName": name, "PolicyDocument": document})

elif (service, command) == ("iam", "attach-role-policy"):
    role = role_or_missing("AttachRolePolicy")
    if opts.get("policy-arn") not in role["attached"]:
        role["attached"].append(opts.get("policy-arn"))
    save(state)
    out({})

elif (service, command) == ("ssm", "create-document"):
    name = opts.get("name")
    if opts.get("document-type") != "Command" or opts.get("document-format") != "JSON":
        bug("create-document needs --document-type Command --document-format JSON")
    if name in state["documents"]:
        fail("DocumentAlreadyExists", "CreateDocument", "The specified document already exists.")
    json.loads(files["content"])
    state["documents"][name] = {"versions": [files["content"]], "default": 1}
    save(state)
    out({"DocumentDescription": {"Name": name, "DocumentVersion": "1", "DefaultVersion": "1"}})

elif (service, command) == ("ssm", "update-document"):
    document = document_or_missing("UpdateDocument")
    if opts.get("document-version") != "$LATEST":
        bug("update-document must name --document-version '$LATEST'")
    json.loads(files["content"])
    if normal(files["content"]) == normal(document["versions"][-1]):
        fail("DuplicateDocumentContent", "UpdateDocument",
             "The content of the association document matches another document.")
    document["versions"].append(files["content"])
    save(state)
    number = str(len(document["versions"]))
    out({"DocumentDescription": {"Name": opts["name"], "DocumentVersion": number}})

elif (service, command) == ("ssm", "update-document-default-version"):
    document = document_or_missing("UpdateDocumentDefaultVersion")
    version = opts.get("document-version")
    known = isinstance(version, str) and version.isdigit()
    if not (known and 1 <= int(version) <= len(document["versions"])):
        fail("InvalidDocumentVersion", "UpdateDocumentDefaultVersion", "no such version")
    document["default"] = int(version)
    save(state)
    out({"Description": {"Name": opts["name"], "DefaultVersion": version}})

elif (service, command) == ("ssm", "get-document"):
    document = document_or_missing("GetDocument")
    version = int(opts.get("document-version", document["default"]))
    out({"Name": opts["name"], "DocumentVersion": str(version), "Status": "Active",
         "Content": document["versions"][version - 1]})

elif (service, command) == ("ssm", "describe-instance-information"):
    match = re.fullmatch(r"Key=InstanceIds,Values=(mi-[0-9a-f]{17})", str(opts.get("filters")))
    if not match:
        bug("describe-instance-information needs --filters Key=InstanceIds,Values=<id>")
    node = state["nodes"].get(match.group(1))
    out({"InstanceInformationList": [{"InstanceId": match.group(1), **node}] if node else []})

elif (service, command) == ("ssm", "create-activation"):
    role = opts.get("iam-role")
    if role not in state["roles"]:
        fail("ValidationException", "CreateActivation", f"Role {role} not found.")
    if opts.get("registration-limit") != "1":
        bug("the activation must be single use")
    stamp = str(opts.get("expiration-date"))
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp):
        bug("the expiration date must be UTC ISO 8601")
    utc = datetime.timezone.utc
    expires = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=utc)
    hours = (expires - datetime.datetime.now(datetime.timezone.utc)).total_seconds() / 3600
    if not 3.9 < hours < 4.1:
        bug(f"the activation must expire in 4 hours, not {hours:.2f}")
    state["activations"].append({"role": role, "name": opts.get("default-instance-name"),
                                 "limit": 1, "hours": hours})
    save(state)
    out({"ActivationId": "00000000-0000-4000-8000-0000000000a1",
         "ActivationCode": "FAKE-ACTIVATION-CODE-0123"})

else:
    bug(f"unsupported call: {service} {command}")
'''.replace("__PYTHON__", sys.executable).replace("__MARKER__", MARKER)


class Aws:
    """The fake ``aws`` plus a way to run the script against it."""

    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.tmp = tmp_path / "scratch"
        self.tmp.mkdir()
        self.state_path = tmp_path / "state.json"
        self.log_path = tmp_path / "log.jsonl"
        fake = self.bin / "aws"
        fake.write_text(FAKE_AWS)
        fake.chmod(0o755)
        self.log_path.write_text("")
        self.save(
            {
                "account": ACCOUNT,
                "providers": [],
                "roles": {},
                "documents": {},
                "activations": [],
                "nodes": {NODE: {"PingStatus": "Online"}},
            }
        )

    def state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text())

    def save(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state))

    def calls(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    def commands(self) -> list[str]:
        return [" ".join(call["argv"][:2]) for call in self.calls()]

    def reset_log(self) -> None:
        self.log_path.write_text("")

    def run(
        self,
        *args: str,
        env: dict[str, str] | None = None,
        script: Path = SCRIPT,
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("AWS_", "FAKE_AWS_"))
        }
        environment.update(
            {
                "PATH": f"{self.bin}:{os.environ['PATH']}",
                "TMPDIR": str(self.tmp),
                "FAKE_AWS_STATE": str(self.state_path),
                "FAKE_AWS_LOG": str(self.log_path),
                "FAKE_AWS_REGION": REGION,
                "AWS_SHARED_CREDENTIALS_FILE": "/nonexistent",
                "AWS_CONFIG_FILE": "/nonexistent",
            }
        )
        environment.update(env or {})
        return subprocess.run(
            [str(script), *args],
            capture_output=True,
            text=True,
            env=environment,
            cwd=self.tmp,
            check=False,
        )

    def apply(
        self, target: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return self.run(
            target, "apply", "--region", REGION, "--node", NODE, "--subject", SUBJECT, env=env
        )

    def check(self, target: str) -> subprocess.CompletedProcess[str]:
        return self.run(target, "check", "--region", REGION, "--node", NODE, "--subject", SUBJECT)


@pytest.fixture
def aws(tmp_path: Path) -> Aws:
    return Aws(tmp_path)


TARGETS = {
    "production": {
        "roles": {
            "csa-prod-deploy-plan": "send-plan-document",
            "csa-prod-deploy-apply": "send-apply-document",
        },
        "documents": ["csa-production-plan", "csa-production-apply"],
    },
    "staging": {
        "roles": {"csa-staging-deploy": "send-staging-documents"},
        "documents": ["csa-staging-plan", "csa-staging-apply"],
    },
}

CREATE_OR_UPDATE = re.compile(r"^(iam|ssm) (create|update|put|attach|delete)-")
READ_ONLY = re.compile(r"^(sts|iam|ssm) (get|list|describe)-")
STEP_LINE = re.compile(r"^\[(\d+)/(\d+)\] (.+): (.+)$")


def step_lines(result: subprocess.CompletedProcess[str]) -> list[re.Match[str]]:
    matches = [STEP_LINE.match(line) for line in result.stdout.splitlines()]
    return [match for match in matches if match]


# --- validation -----------------------------------------------------------------------------

BAD_REGIONS = ["", "zz-test", "ZZ-TEST-1", "zz_test_1", "zz-test-1\n", "zz-test-12", "zz-test-1 x"]
BAD_NODES = ["", "mi-123", "i-0123456789abcdef0", "mi-0123456789ABCDEF0", "mi-0123456789abcdef01"]
BAD_SUBJECTS = ["", "example-owner/CSAV2", "repo:", "repo:a b", "repo:a|b", "repo:a&b", "repo:a;b"]


@pytest.mark.parametrize("region", BAD_REGIONS)
def test_inputs_are_validated_before_any_aws_call_region(aws: Aws, region: str) -> None:
    result = aws.run("staging", "apply", "--region", region, "--node", NODE, "--subject", SUBJECT)
    assert result.returncode == 2
    assert aws.calls() == []


@pytest.mark.parametrize("node", BAD_NODES)
def test_inputs_are_validated_before_any_aws_call_node(aws: Aws, node: str) -> None:
    result = aws.run("staging", "check", "--region", REGION, "--node", node, "--subject", SUBJECT)
    assert result.returncode == 2
    assert aws.calls() == []


@pytest.mark.parametrize("subject", BAD_SUBJECTS)
def test_inputs_are_validated_before_any_aws_call_subject(aws: Aws, subject: str) -> None:
    result = aws.run(
        "production", "apply", "--region", REGION, "--node", NODE, "--subject", subject
    )
    assert result.returncode == 2
    assert aws.calls() == []


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("staging",),
        ("other", "apply", "--region", REGION, "--node", NODE, "--subject", SUBJECT),
        ("staging", "delete", "--region", REGION, "--node", NODE, "--subject", SUBJECT),
        ("staging", "apply", "--node", NODE, "--subject", SUBJECT),
        ("staging", "apply", "--region", REGION, "--subject", SUBJECT),
        ("staging", "apply", "--region", REGION, "--node", NODE),
        ("staging", "check", "--region", REGION, "--node", NODE),
        ("staging", "apply", "--region", REGION, "--node", NODE, "--subject", SUBJECT, "--x", "y"),
        ("staging", "apply", "--region"),
    ],
)
def test_inputs_are_validated_before_any_aws_call_shape(aws: Aws, args: tuple[str, ...]) -> None:
    result = aws.run(*args)
    assert result.returncode == 2
    assert aws.calls() == []


@pytest.mark.parametrize(
    "account", ["", "1234", "12345678901a", "1234567890123", "123456789012\n9"]
)
def test_a_malformed_account_stops_right_after_the_identity_call(aws: Aws, account: str) -> None:
    result = aws.apply("staging", env={"FAKE_AWS_ACCOUNT": account})
    assert result.returncode != 0
    assert aws.commands() == ["sts get-caller-identity"]


def test_an_unexpected_aws_error_stops_the_run_and_names_the_step(aws: Aws) -> None:
    result = aws.apply("staging", env={"FAKE_AWS_DENY": "iam get-role"})
    assert result.returncode == 1
    assert "AccessDenied" in result.stderr
    # The provider step finished before the failing role step; nothing after it ran.
    assert [c for c in aws.commands() if CREATE_OR_UPDATE.match(c)] == [
        "iam create-open-id-connect-provider"
    ]
    assert aws.commands()[-1] == "iam get-role"


# --- apply ----------------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["production", "staging"])
def test_apply_on_an_empty_account_creates_provider_roles_policies_and_documents_in_order(
    aws: Aws, target: str
) -> None:
    spec = TARGETS[target]
    result = aws.apply(target)
    assert result.returncode == 0, result.stderr

    writes = [
        call["argv"] for call in aws.calls() if CREATE_OR_UPDATE.match(" ".join(call["argv"][:2]))
    ]
    expected: list[tuple[str, ...]] = [("iam", "create-open-id-connect-provider")]
    for role, policy in spec["roles"].items():
        expected += [("iam", "create-role", role), ("iam", "put-role-policy", role, policy)]
    for document in spec["documents"]:
        expected.append(("ssm", "create-document", document))
    seen = []
    for argv in writes:
        opts = dict(zip(argv[2::2], argv[3::2], strict=False))
        if argv[1] == "create-open-id-connect-provider":
            seen.append(("iam", argv[1]))
        elif argv[1] == "create-role":
            seen.append(("iam", argv[1], opts["--role-name"]))
        elif argv[1] == "put-role-policy":
            seen.append(("iam", argv[1], opts["--role-name"], opts["--policy-name"]))
        else:
            seen.append(("ssm", argv[1], opts["--name"]))
    assert seen == expected

    state = aws.state()
    assert state["providers"] == [OIDC_ARN]
    assert sorted(state["roles"]) == sorted(spec["roles"])
    for role, policy in spec["roles"].items():
        assert list(state["roles"][role]["policies"]) == [policy]
    assert sorted(state["documents"]) == sorted(spec["documents"])
    for call in aws.calls():
        assert call["argv"][call["argv"].index("--region") + 1] == REGION
    for line in result.stdout.splitlines():
        if line.startswith("Role ARN"):
            assert ACCOUNT in line  # the role ARNs are the one thing printed from AWS
    arns = [line for line in result.stdout.splitlines() if line.startswith("Role ARN")]
    assert len(arns) == len(spec["roles"])
    for role in spec["roles"]:
        assert any(line.endswith(f"arn:aws:iam::{ACCOUNT}:role/{role}") for line in arns)


@pytest.mark.parametrize("target", ["production", "staging"])
def test_apply_twice_makes_no_create_or_update_call_the_second_time(aws: Aws, target: str) -> None:
    assert aws.apply(target).returncode == 0
    state_after_first = aws.state()
    aws.reset_log()

    second = aws.apply(target)

    assert second.returncode == 0, second.stderr
    assert aws.commands()  # it did look
    assert not [c for c in aws.commands() if CREATE_OR_UPDATE.match(c)]
    assert aws.state() == state_after_first
    results = {match.group(4) for match in step_lines(second)}
    assert "unchanged" in results
    assert not results & {"created", "updated"}


def test_apply_creates_only_the_provider_when_the_rest_exists(aws: Aws) -> None:
    assert aws.apply("staging").returncode == 0
    state = aws.state()
    state["providers"] = []
    aws.save(state)
    aws.reset_log()

    assert aws.apply("staging").returncode == 0

    assert [c for c in aws.commands() if CREATE_OR_UPDATE.match(c)] == [
        "iam create-open-id-connect-provider"
    ]


@pytest.mark.parametrize("target", ["production", "staging"])
def test_a_changed_document_is_updated_and_made_the_default(aws: Aws, target: str) -> None:
    assert aws.apply(target).returncode == 0
    name = TARGETS[target]["documents"][0]
    state = aws.state()
    old = json.loads(state["documents"][name]["versions"][0])
    old["description"] = "an older description"
    state["documents"][name]["versions"][0] = json.dumps(old, indent=2)
    aws.save(state)
    aws.reset_log()

    result = aws.apply(target)

    assert result.returncode == 0, result.stderr
    changes = [c for c in aws.commands() if CREATE_OR_UPDATE.match(c)]
    assert changes == ["ssm update-document", "ssm update-document-default-version"]
    update, default = [c for c in aws.calls() if CREATE_OR_UPDATE.match(" ".join(c["argv"][:2]))]
    assert "$LATEST" in update["argv"]
    assert update["argv"][update["argv"].index("--document-version") + 1] == "$LATEST"
    assert default["argv"][default["argv"].index("--document-version") + 1] == "2"
    document = aws.state()["documents"][name]
    assert document["default"] == 2
    assert json.loads(document["versions"][1]) == json.loads(
        (ROOT / "deploy" / target / f"ssm-document-{name.rsplit('-', 1)[1]}.json").read_text()
    )
    assert "updated" in {m.group(4) for m in step_lines(result)}
    # And again: now identical, so no write.
    aws.reset_log()
    assert aws.apply(target).returncode == 0
    assert not [c for c in aws.commands() if CREATE_OR_UPDATE.match(c)]


def test_a_document_with_only_different_formatting_counts_as_unchanged(aws: Aws) -> None:
    assert aws.apply("staging").returncode == 0
    state = aws.state()
    name = "csa-staging-plan"
    state["documents"][name]["versions"][0] = json.dumps(
        json.loads(state["documents"][name]["versions"][0]), sort_keys=True, separators=(",", ":")
    )
    aws.save(state)
    aws.reset_log()

    assert aws.apply("staging").returncode == 0
    assert not [c for c in aws.commands() if CREATE_OR_UPDATE.match(c)]


def test_a_changed_role_trust_and_policy_are_updated(aws: Aws) -> None:
    assert aws.apply("staging").returncode == 0
    state = aws.state()
    state["roles"]["csa-staging-deploy"]["trust"]["Statement"][0]["Effect"] = "Deny"
    state["roles"]["csa-staging-deploy"]["policies"]["send-staging-documents"]["Statement"] = []
    aws.save(state)
    aws.reset_log()

    assert aws.apply("staging").returncode == 0

    assert [c for c in aws.commands() if CREATE_OR_UPDATE.match(c)] == [
        "iam update-assume-role-policy",
        "iam put-role-policy",
    ]
    again = aws.state()["roles"]["csa-staging-deploy"]
    assert again["trust"]["Statement"][0]["Effect"] == "Allow"
    assert again["policies"]["send-staging-documents"]["Statement"]


def test_apply_works_when_the_working_directory_is_not_the_repository(aws: Aws) -> None:
    # The fixture runs the script with cwd in a scratch directory already; resolve via a symlink
    # to be sure the templates are found relative to the script's real path.
    link = aws.tmp / "setup-target-link.sh"
    link.symlink_to(SCRIPT)
    result = aws.run(
        "staging", "apply", "--region", REGION, "--node", NODE, "--subject", SUBJECT, script=link
    )
    assert result.returncode == 0, result.stderr


# --- check ----------------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["production", "staging"])
def test_check_makes_only_read_calls_and_reports_each_missing_or_differing_item(
    aws: Aws, target: str
) -> None:
    spec = TARGETS[target]
    state = aws.state()
    state["nodes"] = {}
    aws.save(state)

    missing = aws.check(target)

    assert missing.returncode == 1
    assert aws.commands()
    assert all(READ_ONLY.match(command) for command in aws.commands()), aws.commands()
    results = [match.group(4) for match in step_lines(missing)]
    assert results.count("missing") == 1 + 2 * len(spec["roles"]) + len(spec["documents"]) + 1
    assert not {"differs", "ok"} & set(results)
    whats = [match.group(3) for match in step_lines(missing)]
    for role in spec["roles"]:
        assert any(role in what and "trust" in what for what in whats)
        assert any(role in what and "policy" in what for what in whats)
    for document in spec["documents"]:
        assert any(document in what for what in whats)
    assert any("node" in what.lower() for what in whats)
    assert aws.state() == state

    # Everything present, then break one of each kind.
    assert aws.apply(target).returncode == 0
    state = aws.state()
    state["nodes"] = {NODE: {"PingStatus": "ConnectionLost"}}
    first_role = next(iter(spec["roles"]))
    state["roles"][first_role]["trust"]["Statement"][0]["Effect"] = "Deny"
    state["roles"][first_role]["policies"][spec["roles"][first_role]]["Statement"] = []
    document = spec["documents"][0]
    old = json.loads(state["documents"][document]["versions"][0])
    old["description"] = "something else"
    state["documents"][document]["versions"][0] = json.dumps(old)
    aws.save(state)
    aws.reset_log()

    differing = aws.check(target)

    assert differing.returncode == 1
    assert all(READ_ONLY.match(command) for command in aws.commands()), aws.commands()
    results = [match.group(4) for match in step_lines(differing)]
    assert results.count("differs") == 4
    assert "missing" not in results
    assert aws.state() == state


@pytest.mark.parametrize("target", ["production", "staging"])
def test_check_passes_on_a_complete_target(aws: Aws, target: str) -> None:
    assert aws.apply(target).returncode == 0
    aws.reset_log()

    result = aws.check(target)

    assert result.returncode == 0, result.stdout + result.stderr
    assert {m.group(4) for m in step_lines(result)} == {"checking", "ok"}
    assert all(READ_ONLY.match(command) for command in aws.commands())
    assert not [c for c in aws.commands() if CREATE_OR_UPDATE.match(c)]


def test_check_reports_a_missing_provider_alone(aws: Aws) -> None:
    assert aws.apply("staging").returncode == 0
    state = aws.state()
    state["providers"] = []
    aws.save(state)

    result = aws.check("staging")

    assert result.returncode == 1
    assert [m.group(4) for m in step_lines(result)].count("missing") == 1


# --- activate -------------------------------------------------------------------------------

NODE_ROLES = {
    "production": ("csa-prod-ssm-node", "csa-prod"),
    "staging": ("csa-staging-ssm-node", "csa-staging"),
}
SSM_CORE = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"


def activate(aws: Aws, target: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return aws.run(target, "activate", "--region", REGION, *extra)


@pytest.mark.parametrize("target", ["production", "staging"])
def test_activate_creates_the_node_role_once_and_a_single_use_activation(
    aws: Aws, target: str
) -> None:
    role, instance_name = NODE_ROLES[target]

    first = activate(aws, target)

    assert first.returncode == 0, first.stderr
    calls = aws.calls()
    assert [" ".join(c["argv"][:2]) for c in calls] == [
        "sts get-caller-identity",
        "iam get-role",
        "iam create-role",
        "iam attach-role-policy",
        "ssm create-activation",
    ]
    create, attach, activation = calls[2], calls[3], calls[4]
    assert create["argv"][create["argv"].index("--role-name") + 1] == role
    trust = json.loads(create["files"]["assume-role-policy-document"])
    assert trust == {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ssm.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": ACCOUNT},
                    "ArnEquals": {"aws:SourceArn": f"arn:aws:ssm:{REGION}:{ACCOUNT}:*"},
                },
            }
        ],
    }
    assert attach["argv"][attach["argv"].index("--role-name") + 1] == role
    assert attach["argv"][attach["argv"].index("--policy-arn") + 1] == SSM_CORE
    argv = activation["argv"]
    assert argv[argv.index("--iam-role") + 1] == role
    assert argv[argv.index("--default-instance-name") + 1] == instance_name
    assert argv[argv.index("--registration-limit") + 1] == "1"
    assert "--expiration-date" in argv  # the fake checks it is UTC and about four hours away
    assert aws.state()["roles"][role]["attached"] == [SSM_CORE]
    assert len(aws.state()["activations"]) == 1

    # A second run keeps the role (no create-role) and issues one more activation.
    aws.reset_log()
    second = activate(aws, target)
    assert second.returncode == 0, second.stderr
    assert aws.commands() == [
        "sts get-caller-identity",
        "iam get-role",
        "iam attach-role-policy",
        "ssm create-activation",
    ]
    assert len(aws.state()["activations"]) == 2
    assert list(aws.state()["roles"]) == [role]


def test_activate_prints_the_id_and_code_once_with_the_warning(aws: Aws) -> None:
    result = activate(aws, "production")

    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert out.count("00000000-0000-4000-8000-0000000000a1") == 1
    assert out.count("FAKE-ACTIVATION-CODE-0123") == 1
    assert "type the code at the hidden prompt on the host; never paste it anywhere else" in out
    assert "FAKE-ACTIVATION-CODE-0123" not in result.stderr
    assert MARKER not in out + result.stderr
    others = [
        line
        for line in out.splitlines()
        if not STEP_LINE.match(line)
        and not line.startswith(("Activation id: ", "Activation code: "))
    ]
    assert others == [
        "type the code at the hidden prompt on the host; never paste it anywhere else"
    ]
    steps = step_lines(result)
    assert {int(m.group(2)) for m in steps} == {3}
    for number in (1, 2, 3):
        assert len([m for m in steps if int(m.group(1)) == number]) == 2
    # The code is in the output only after the activation call returned: no step line holds it.
    assert not any("FAKE-ACTIVATION" in line.group(0) for line in steps)


def test_activate_needs_no_node_or_subject_but_checks_them_when_given(aws: Aws) -> None:
    assert activate(aws, "staging", "--node", NODE, "--subject", SUBJECT).returncode == 0
    aws.reset_log()
    for bad in (("--node", "mi-123"), ("--subject", "repo:a b")):
        result = activate(aws, "staging", *bad)
        assert result.returncode == 2
    assert aws.calls() == []


@pytest.mark.parametrize("region", BAD_REGIONS)
def test_activate_validates_the_region_before_any_aws_call(aws: Aws, region: str) -> None:
    result = aws.run("production", "activate", "--region", region)
    assert result.returncode == 2
    assert aws.calls() == []


def test_a_failed_activation_prints_no_code_and_leaves_no_temporary_files(aws: Aws) -> None:
    result = aws.run(
        "staging", "activate", "--region", REGION, env={"FAKE_AWS_DENY": "ssm create-activation"}
    )
    assert result.returncode == 1
    assert "AccessDenied" in result.stderr
    assert "Activation code" not in result.stdout
    assert list(aws.tmp.iterdir()) == []


def test_activate_node_role_names_match_the_runbook() -> None:
    step1 = _runbook_section("### 1. AWS (CloudShell, admin login)", "### 2. Instance")
    assert "--role-name csa-prod-ssm-node" in step1
    assert "--default-instance-name csa-prod " in step1
    assert "--registration-limit 1" in step1
    assert "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" in step1
    assert '"aws:SourceAccount"' in step1 and '"aws:SourceArn"' in step1


# --- the filled files and the names ---------------------------------------------------------


@pytest.mark.parametrize("target", ["production", "staging"])
def test_filled_files_have_no_placeholder_and_never_land_in_the_repository(
    aws: Aws, target: str
) -> None:
    before = subprocess.run(
        ["git", "status", "--porcelain", "--", "deploy/aws"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    ).stdout
    result = aws.apply(target)
    assert result.returncode == 0, result.stderr

    sent = [text for call in aws.calls() for text in call["files"].values()]
    assert sent
    for text in sent:
        assert not re.search(r"<[^>]*>", text)
        json.loads(text)
    joined = "\n".join(sent)
    assert ACCOUNT in joined and NODE in joined and SUBJECT in joined and REGION in joined
    assert OIDC_ARN in joined
    assert "|" not in SUBJECT and "&" not in SUBJECT  # the escape is exercised in the next test
    after = subprocess.run(
        ["git", "status", "--porcelain", "--", "deploy/aws"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    ).stdout
    assert after == before
    assert list(aws.tmp.iterdir()) == []  # the working directory is gone
    for call in aws.calls():
        for name, text in call["files"].items():
            assert text  # every file:// argument pointed at a real, filled file
            assert f"--{name}" in call["argv"]
        for arg in call["argv"]:
            if arg.startswith("file://"):
                assert not arg.startswith(f"file://{ROOT}")


def test_the_working_directory_is_removed_after_a_failure_too(aws: Aws) -> None:
    result = aws.apply("staging", env={"FAKE_AWS_DENY": "iam create-role"})
    assert result.returncode == 1
    assert list(aws.tmp.iterdir()) == []


def test_a_template_with_an_unknown_placeholder_is_refused_before_any_write(
    aws: Aws, tmp_path: Path
) -> None:
    # A copy of the script with a template that has a placeholder the script does not fill.
    copy = tmp_path / "repo"
    (copy / "deploy" / "aws").mkdir(parents=True)
    (copy / "deploy" / "aws" / "setup-target.sh").write_bytes(SCRIPT.read_bytes())
    (copy / "deploy" / "aws" / "setup-target.sh").chmod(0o755)
    for source in (ROOT / "deploy" / "staging").rglob("*.json"):
        target = copy / "deploy" / "staging" / source.relative_to(ROOT / "deploy" / "staging")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text())
    broken = copy / "deploy" / "staging" / "ssm-document-plan.json"
    broken.write_text(
        broken.read_text().replace('"schemaVersion"', '"<surprise>": 1, "schemaVersion"')
    )

    result = aws.run(
        "staging",
        "apply",
        "--region",
        REGION,
        "--node",
        NODE,
        "--subject",
        SUBJECT,
        script=copy / "deploy" / "aws" / "setup-target.sh",
    )

    assert result.returncode == 1
    assert "placeholder" in result.stderr
    assert not [c for c in aws.commands() if CREATE_OR_UPDATE.match(c)]


def _runbook_section(start: str, end: str) -> str:
    text = PRODUCTION_DOC.read_text()
    return text[text.index(start) : text.index(end)]


def test_production_names_match_the_runbook(aws: Aws) -> None:
    step3 = _runbook_section("### 3. AWS (CloudShell)", "### 4. AWS (CloudShell)")
    step4 = _runbook_section("### 4. AWS (CloudShell)", "### 5. Instance")
    runbook_roles = re.findall(
        r"aws iam create-role --role-name (\S+) --assume-role-policy-document file://(\S+)", step3
    )
    runbook_policies = re.findall(
        r"aws iam put-role-policy --role-name (\S+) --policy-name (\S+) --policy-document file://(\S+)",
        step3,
    )
    runbook_documents = re.findall(
        r"aws ssm create-document .*--name (\S+) .*--content file://(\S+)", step4
    )
    assert len(runbook_roles) == 2 and len(runbook_policies) == 2 and len(runbook_documents) == 2

    assert aws.apply("production").returncode == 0

    calls = aws.calls()
    created_roles = {
        c["argv"][c["argv"].index("--role-name") + 1]: c
        for c in calls
        if c["argv"][:2] == ["iam", "create-role"]
    }
    put_policies = {
        c["argv"][c["argv"].index("--role-name") + 1]: c
        for c in calls
        if c["argv"][:2] == ["iam", "put-role-policy"]
    }
    created_documents = {
        c["argv"][c["argv"].index("--name") + 1]: c
        for c in calls
        if c["argv"][:2] == ["ssm", "create-document"]
    }
    assert sorted(created_roles) == sorted(name for name, _ in runbook_roles)
    for role, policy, policy_file in runbook_policies:
        assert (
            put_policies[role]["argv"][put_policies[role]["argv"].index("--policy-name") + 1]
            == policy
        )
        # The filled file is the runbook's file: same document as the repository template.
        template = ROOT / "deploy" / "production" / "iam" / policy_file
        sent = json.loads(put_policies[role]["files"]["policy-document"])
        assert [s["Sid"] for s in sent["Statement"]] == [
            s["Sid"] for s in json.loads(template.read_text())["Statement"]
        ]
    for role, trust_file in runbook_roles:
        template = json.loads((ROOT / "deploy" / "production" / "iam" / trust_file).read_text())
        environment = template["Statement"][0]["Condition"]["StringEquals"][
            f"{OIDC_HOST}:sub"
        ].split(":environment:")[1]
        sent = json.loads(created_roles[role]["files"]["assume-role-policy-document"])
        assert (
            sent["Statement"][0]["Condition"]["StringEquals"][f"{OIDC_HOST}:sub"]
            == f"{SUBJECT}:environment:{environment}"
        )
    assert sorted(created_documents) == sorted(name for name, _ in runbook_documents)
    for name, document_file in runbook_documents:
        template = json.loads((ROOT / "deploy" / "production" / document_file).read_text())
        assert json.loads(created_documents[name]["files"]["content"]) == template


def test_the_staging_role_trusts_only_staging_and_names_only_staging_documents(aws: Aws) -> None:
    assert aws.apply("staging").returncode == 0

    calls = aws.calls()
    [create] = [c for c in calls if c["argv"][:2] == ["iam", "create-role"]]
    trust = json.loads(create["files"]["assume-role-policy-document"])
    [statement] = trust["Statement"]
    assert statement["Principal"] == {"Federated": OIDC_ARN}
    assert statement["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert statement["Condition"]["StringEquals"] == {
        f"{OIDC_HOST}:aud": "sts.amazonaws.com",
        f"{OIDC_HOST}:sub": f"{SUBJECT}:environment:staging",
    }
    [put] = [c for c in calls if c["argv"][:2] == ["iam", "put-role-policy"]]
    policy = json.loads(put["files"]["policy-document"])
    send = next(s for s in policy["Statement"] if s["Action"] == "ssm:SendCommand")
    assert sorted(send["Resource"]) == sorted(
        [
            f"arn:aws:ssm:{REGION}:{ACCOUNT}:document/csa-staging-plan",
            f"arn:aws:ssm:{REGION}:{ACCOUNT}:document/csa-staging-apply",
            f"arn:aws:ssm:{REGION}:{ACCOUNT}:managed-instance/{NODE}",
        ]
    )
    assert "production" not in json.dumps(policy) + json.dumps(trust)
    documents = [c for c in calls if c["argv"][:2] == ["ssm", "create-document"]]
    assert [d["argv"][d["argv"].index("--name") + 1] for d in documents] == [
        "csa-staging-plan",
        "csa-staging-apply",
    ]
    for document in documents:
        text = document["files"]["content"]
        assert "csa-prod" not in text and "production" not in text.lower().replace(
            "not production", ""
        )


def test_subject_characters_that_matter_to_sed_are_rejected_or_escaped(aws: Aws) -> None:
    # `|` and `&` are refused by validation; `.` and `@` are legal and must land verbatim.
    subject = "repo:example.owner@1001/CSA-V2.x@2002"
    result = aws.run("staging", "apply", "--region", REGION, "--node", NODE, "--subject", subject)
    assert result.returncode == 0, result.stderr
    [create] = [c for c in aws.calls() if c["argv"][:2] == ["iam", "create-role"]]
    trust = json.loads(create["files"]["assume-role-policy-document"])
    assert (
        trust["Statement"][0]["Condition"]["StringEquals"][f"{OIDC_HOST}:sub"]
        == f"{subject}:environment:staging"
    )


# --- output ---------------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["production", "staging"])
def test_every_step_is_printed_and_nothing_else_from_aws(aws: Aws, target: str) -> None:
    spec = TARGETS[target]
    result = aws.apply(target)
    assert result.returncode == 0

    assert MARKER not in result.stdout + result.stderr
    assert result.stderr == ""
    steps = step_lines(result)
    total = 1 + 2 * len(spec["roles"]) + len(spec["documents"])
    assert {int(m.group(2)) for m in steps} == {total}
    for number in range(1, total + 1):
        lines = [m for m in steps if int(m.group(1)) == number]
        assert len(lines) == 2, (number, lines)  # one before its calls, one after
        assert lines[1].group(4) in {"created", "updated", "unchanged"}
    others = [
        line
        for line in result.stdout.splitlines()
        if not STEP_LINE.match(line) and not line.startswith("Role ARN ")
    ]
    assert all(line == "" or line.startswith("apply ") for line in others), others
    # Account and node ids appear nowhere in the output apart from the role ARNs.
    for line in result.stdout.splitlines():
        if not line.startswith("Role ARN "):
            assert ACCOUNT not in line and NODE not in line


def test_check_output_keeps_to_the_same_grammar(aws: Aws) -> None:
    result = aws.check("staging")
    assert MARKER not in result.stdout + result.stderr
    steps = step_lines(result)
    total = 1 + 2 + 2 + 1
    assert {int(m.group(2)) for m in steps} == {total}
    for number in range(1, total + 1):
        assert len([m for m in steps if int(m.group(1)) == number]) == 2


# --- the script itself ----------------------------------------------------------------------


def test_script_is_bash_strict_quoted_and_executable() -> None:
    text = SCRIPT.read_text()
    lines = text.splitlines()
    code = "\n".join(line for line in lines if not line.lstrip().startswith("#"))

    assert lines[0] == "#!/bin/bash"
    strict = lines.index("set -euo pipefail")
    locale = lines.index("export LC_ALL=C")
    assert strict < locale < next(i for i, line in enumerate(lines) if "=~" in line)
    assert not re.search(r"\beval\b|\b(?:ba)?sh -c\b|\bset -x\b|\bsource\b", code)
    assert "jq" in code
    assert _unquoted_expansions(text) == []
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    assert os.access(SCRIPT, os.X_OK)
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    tracked = subprocess.run(
        ["git", "ls-files", "-s", str(SCRIPT)], capture_output=True, text=True, cwd=ROOT, check=True
    ).stdout
    assert tracked == "" or tracked.startswith("100755")


def test_script_writes_nothing_under_the_repository_and_makes_no_write_call_in_check() -> None:
    code = "\n".join(
        line for line in SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    assert "mktemp -d" in code
    assert not re.search(r">\s*\"?\$\{?ROOT", code)
    assert not re.search(r"\bsed -i\b[^\n]*\$\{?ROOT", code)
