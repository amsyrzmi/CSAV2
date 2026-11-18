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
    # The real CLI starts its error text with a blank line.
    message = f"An error occurred ({code}) when calling the {operation} operation: {text}"
    sys.stderr.write(f"\n{message}\n")
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
if os.environ.get("FAKE_AWS_SILENT") == f"{service} {command}":
    sys.stderr.write("\n  \n")
    sys.exit(254)

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
    version = opts.get("document-version", document["default"])
    version = len(document["versions"]) if version == "$LATEST" else int(version)
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
    # The real CLI's text starts with a blank line; the reason is its first non-blank line.
    assert re.search(r"failed: An error occurred", result.stderr), result.stderr
    # The live trusts are read before the first write (the removal guard), so a failing read
    # stops the run with nothing written, not even the provider.
    assert [c for c in aws.commands() if CREATE_OR_UPDATE.match(c)] == []
    assert aws.commands()[-1] == "iam get-role"


def test_a_later_unexpected_aws_error_names_its_step_after_the_earlier_ones_finished(
    aws: Aws,
) -> None:
    result = aws.apply("staging", env={"FAKE_AWS_DENY": "iam put-role-policy"})
    assert result.returncode == 1
    assert "[3/5] role csa-staging-deploy policy send-staging-documents: failed:" in result.stderr
    # The provider and the role were written before the failing policy step; nothing after it ran.
    assert [c for c in aws.commands() if CREATE_OR_UPDATE.match(c)] == [
        "iam create-open-id-connect-provider",
        "iam create-role",
        "iam put-role-policy",
    ]


def test_an_aws_error_with_no_text_still_says_something(aws: Aws) -> None:
    result = aws.apply("staging", env={"FAKE_AWS_SILENT": "iam get-role"})
    assert result.returncode == 1
    assert re.search(r"failed: \S", result.stderr), result.stderr


def test_a_failed_identity_call_prints_a_plain_message(aws: Aws) -> None:
    result = aws.apply("staging", env={"FAKE_AWS_DENY": "sts get-caller-identity"})
    assert result.returncode == 1
    assert result.stderr.splitlines()[0].startswith(
        "could not read the caller identity: An error occurred"
    )
    assert "[0/0]" not in result.stderr
    assert aws.commands() == ["sts get-caller-identity"]


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


def test_an_interrupted_document_update_is_finished_by_a_rerun(aws: Aws) -> None:
    assert aws.apply("staging").returncode == 0
    name = "csa-staging-plan"
    state = aws.state()
    current = state["documents"][name]["versions"][0]
    old = json.loads(current)
    old["description"] = "an older description"
    # update-document succeeded (a new version holds the right content), but the default was
    # never moved: AWS would now reject another update-document as a duplicate.
    state["documents"][name]["versions"] = [json.dumps(old), json.dumps(old, indent=2), current]
    state["documents"][name]["default"] = 1
    aws.save(state)
    aws.reset_log()

    check = aws.check("staging")
    assert check.returncode == 1
    assert "differs" in {m.group(4) for m in step_lines(check)}
    assert all(READ_ONLY.match(command) for command in aws.commands()), aws.commands()
    aws.reset_log()

    result = aws.apply("staging")

    assert result.returncode == 0, result.stderr
    [write] = [c for c in aws.calls() if CREATE_OR_UPDATE.match(" ".join(c["argv"][:2]))]
    assert write["argv"][:2] == ["ssm", "update-document-default-version"]
    assert write["argv"][write["argv"].index("--document-version") + 1] == "3"
    assert aws.state()["documents"][name]["default"] == 3
    assert len(aws.state()["documents"][name]["versions"]) == 3
    assert "updated" in {m.group(4) for m in step_lines(result)}
    aws.reset_log()
    assert aws.apply("staging").returncode == 0
    assert not [c for c in aws.commands() if CREATE_OR_UPDATE.match(c)]


def test_a_stale_latest_version_is_not_mistaken_for_the_right_content(aws: Aws) -> None:
    assert aws.apply("staging").returncode == 0
    name = "csa-staging-plan"
    state = aws.state()
    current = state["documents"][name]["versions"][0]
    old = json.loads(current)
    old["description"] = "an older description"
    state["documents"][name]["versions"] = [json.dumps(old), json.dumps(old, indent=2)]
    aws.save(state)
    aws.reset_log()

    assert aws.apply("staging").returncode == 0

    assert [c for c in aws.commands() if CREATE_OR_UPDATE.match(c)] == [
        "ssm update-document",
        "ssm update-document-default-version",
    ]


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
    # The code is on stdout only: no file anywhere under the temp dir or the test directory.
    for path in aws.tmp.parent.rglob("*"):
        if path.is_file() and aws.bin not in path.parents:  # the fake aws holds the literal
            assert b"FAKE-ACTIVATION-CODE-0123" not in path.read_bytes(), path
    assert list(aws.tmp.iterdir()) == []
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


WAIT_HINT = (
    "if the role was only just created, wait a minute and run the same command again "
    "(it will say exists and continue)"
)


def test_a_failed_activation_after_creating_the_role_says_to_wait_and_rerun(aws: Aws) -> None:
    result = aws.run(
        "staging", "activate", "--region", REGION, env={"FAKE_AWS_DENY": "ssm create-activation"}
    )
    assert result.returncode == 1
    assert WAIT_HINT in result.stderr.splitlines()
    assert "iam create-role" in aws.commands()
    assert aws.commands()[-1] == "ssm create-activation"  # no retry loop


def test_a_failed_activation_with_an_existing_role_gives_no_wait_hint(aws: Aws) -> None:
    assert activate(aws, "staging").returncode == 0
    aws.reset_log()
    result = aws.run(
        "staging", "activate", "--region", REGION, env={"FAKE_AWS_DENY": "ssm create-activation"}
    )
    assert result.returncode == 1
    assert WAIT_HINT not in result.stderr
    assert "iam create-role" not in aws.commands()


def test_activate_node_role_names_match_the_runbook() -> None:
    step1 = _runbook_section("### 1. AWS (CloudShell, admin login)", "### 2. Instance")
    assert "--role-name csa-prod-ssm-node" in step1
    assert "--default-instance-name csa-prod " in step1
    assert "--registration-limit 1" in step1
    assert "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" in step1
    assert '"aws:SourceAccount"' in step1 and '"aws:SourceArn"' in step1


# --- several subjects (an organisation move) ------------------------------------------------

SUBJECT_A = SUBJECT
SUBJECT_B = "repo:example-org@3003/CSAV2@2002"
SUBJECT_C = "repo:example-org@3003/example-repo@2002"
SUB_KEY = f"{OIDC_HOST}:sub"
ENVIRONMENTS = {
    "csa-prod-deploy-plan": "production-plan",
    "csa-prod-deploy-apply": "production",
    "csa-staging-deploy": "staging",
}


def with_subjects(
    aws: Aws,
    target: str,
    action: str,
    *subjects: str,
    env: dict[str, str] | None = None,
    allow_removal: bool = False,
    script: Path = SCRIPT,
) -> subprocess.CompletedProcess[str]:
    args = ["--region", REGION, "--node", NODE]
    for subject in subjects:
        args += ["--subject", subject]
    if allow_removal:
        args.append("--allow-subject-removal")
    return aws.run(target, action, *args, env=env, script=script)


def stored_sub(aws: Aws, role: str) -> Any:
    trust = aws.state()["roles"][role]["trust"]
    return trust["Statement"][0]["Condition"]["StringEquals"][SUB_KEY]


def set_stored_sub(aws: Aws, role: str, value: Any) -> None:
    state = aws.state()
    state["roles"][role]["trust"]["Statement"][0]["Condition"]["StringEquals"][SUB_KEY] = value
    aws.save(state)


def expected_list(role: str, *subjects: str) -> list[str]:
    return [f"{subject}:environment:{ENVIRONMENTS[role]}" for subject in subjects]


def trust_result(result: subprocess.CompletedProcess[str], role: str) -> str:
    lines = [m for m in step_lines(result) if m.group(3) == f"role {role} trust"]
    assert len(lines) == 2, lines
    return lines[-1].group(4)


@pytest.mark.parametrize("action", ["apply", "check"])
@pytest.mark.parametrize(
    "subjects",
    [
        (SUBJECT_A, SUBJECT_A),
        (SUBJECT_A, SUBJECT_B, SUBJECT_A),
        (SUBJECT_A, SUBJECT_B, SUBJECT_C, SUBJECT_A + "x", SUBJECT_B + "x", SUBJECT_C + "x"),
        (SUBJECT_A, SUBJECT_B, "repo:a b"),
        (SUBJECT_A, "repo:a|b", SUBJECT_C),
        (SUBJECT_A, ""),
        ("example-owner/CSAV2", SUBJECT_B),
    ],
    ids=["twice", "duplicate-among-three", "six", "bad-last", "bad-middle", "empty", "bad-first"],
)
def test_several_subjects_are_validated_before_any_aws_call(
    aws: Aws, action: str, subjects: tuple[str, ...]
) -> None:
    result = with_subjects(aws, "production", action, *subjects)

    assert result.returncode == 2
    assert result.stderr.startswith("--subject ")
    assert aws.calls() == []


def test_five_subjects_are_accepted_and_the_refusals_name_the_problem(aws: Aws) -> None:
    five = [f"repo:example-org@{n}/CSAV2@2002" for n in range(5)]
    assert with_subjects(aws, "staging", "check", *five).returncode == 1  # ran; nothing exists
    assert aws.calls()
    aws.reset_log()

    duplicate = with_subjects(aws, "staging", "check", SUBJECT_A, SUBJECT_A)
    assert "twice" in duplicate.stderr
    too_many = with_subjects(aws, "staging", "check", *five, SUBJECT_A)
    assert "at most 5" in too_many.stderr
    assert aws.calls() == []


def test_activate_checks_every_subject_it_is_given(aws: Aws) -> None:
    ok = activate(aws, "staging", "--subject", SUBJECT_A, "--subject", SUBJECT_B)
    assert ok.returncode == 0, ok.stderr
    aws.reset_log()
    for extra in (
        ("--subject", SUBJECT_A, "--subject", "repo:a b"),
        ("--subject", SUBJECT_A, "--subject", SUBJECT_A),
    ):
        assert activate(aws, "staging", *extra).returncode == 2
    assert aws.calls() == []


@pytest.mark.parametrize("target", ["production", "staging"])
def test_apply_on_an_empty_account_with_three_subjects_writes_the_list(
    aws: Aws, target: str
) -> None:
    subjects = (SUBJECT_A, SUBJECT_B, SUBJECT_C)

    result = with_subjects(aws, target, "apply", *subjects)

    assert result.returncode == 0, result.stderr
    creates = [c for c in aws.calls() if c["argv"][:2] == ["iam", "create-role"]]
    assert len(creates) == len(TARGETS[target]["roles"])
    for create in creates:
        role = create["argv"][create["argv"].index("--role-name") + 1]
        text = create["files"]["assume-role-policy-document"]
        assert not re.search(r"<[^>]*>", text)
        [statement] = json.loads(text)["Statement"]
        assert statement["Principal"] == {"Federated": OIDC_ARN}
        assert statement["Action"] == "sts:AssumeRoleWithWebIdentity"
        assert statement["Condition"]["StringEquals"] == {
            f"{OIDC_HOST}:aud": "sts.amazonaws.com",
            SUB_KEY: expected_list(role, *subjects),
        }
        assert stored_sub(aws, role) == expected_list(role, *subjects)
    # Nothing but the trust documents carries a subject.
    for call in aws.calls():
        for name, text in call["files"].items():
            if name != "assume-role-policy-document":
                assert "repo:" not in text


def test_a_single_subject_still_writes_a_plain_string_and_the_same_document(aws: Aws) -> None:
    assert with_subjects(aws, "production", "apply", SUBJECT_A).returncode == 0

    for role in TARGETS["production"]["roles"]:
        assert stored_sub(aws, role) == f"{SUBJECT_A}:environment:{ENVIRONMENTS[role]}"
    [plan] = [
        c
        for c in aws.calls()
        if c["argv"][:2] == ["iam", "create-role"] and "csa-prod-deploy-plan" in c["argv"]
    ]
    template = (ROOT / "deploy" / "production" / "iam" / "plan-trust-policy.json").read_text()
    expected = template.replace("<repo-subject>", SUBJECT_A).replace(
        "<oidc-provider-arn>", OIDC_ARN
    )
    assert plan["files"]["assume-role-policy-document"] == expected


def test_two_subjects_on_the_staging_role(aws: Aws) -> None:
    assert with_subjects(aws, "staging", "apply", SUBJECT_B, SUBJECT_A).returncode == 0

    assert stored_sub(aws, "csa-staging-deploy") == [
        f"{SUBJECT_B}:environment:staging",
        f"{SUBJECT_A}:environment:staging",
    ]
    assert with_subjects(aws, "staging", "check", SUBJECT_B, SUBJECT_A).returncode == 0
    assert with_subjects(aws, "staging", "check", SUBJECT_A).returncode == 1


@pytest.mark.parametrize("target", ["production", "staging"])
def test_check_treats_the_live_list_as_a_set(aws: Aws, target: str) -> None:
    subjects = (SUBJECT_A, SUBJECT_B, SUBJECT_C)
    assert with_subjects(aws, target, "apply", *subjects).returncode == 0
    for role in TARGETS[target]["roles"]:
        set_stored_sub(aws, role, list(reversed(expected_list(role, *subjects))))
    aws.reset_log()

    same_order = with_subjects(aws, target, "check", *subjects)
    other_order = with_subjects(aws, target, "check", SUBJECT_C, SUBJECT_A, SUBJECT_B)

    for result in (same_order, other_order):
        assert result.returncode == 0, result.stdout + result.stderr
        assert {m.group(4) for m in step_lines(result)} == {"checking", "ok"}
    assert all(READ_ONLY.match(command) for command in aws.commands())


@pytest.mark.parametrize(
    "given",
    [
        (SUBJECT_A, SUBJECT_B),
        (SUBJECT_A, SUBJECT_B, SUBJECT_C, "repo:example-org@4004/CSAV2@2002"),
        (SUBJECT_A, SUBJECT_B, "repo:example-org@4004/CSAV2@2002"),
        (SUBJECT_A,),
        (SUBJECT_C, SUBJECT_B),
    ],
    ids=["missing-one", "extra-one", "one-swapped", "only-one-given", "other-pair"],
)
def test_check_says_differs_for_a_missing_or_extra_subject(
    aws: Aws, given: tuple[str, ...]
) -> None:
    assert (
        with_subjects(aws, "production", "apply", SUBJECT_A, SUBJECT_B, SUBJECT_C).returncode == 0
    )
    aws.reset_log()

    result = with_subjects(aws, "production", "check", *given)

    assert result.returncode == 1
    for role in TARGETS["production"]["roles"]:
        assert trust_result(result, role) == "differs"
    assert all(READ_ONLY.match(command) for command in aws.commands())


def test_a_string_is_not_the_same_as_a_one_element_list(aws: Aws) -> None:
    assert with_subjects(aws, "staging", "apply", SUBJECT_A).returncode == 0
    set_stored_sub(aws, "csa-staging-deploy", [f"{SUBJECT_A}:environment:staging"])
    aws.reset_log()

    check = with_subjects(aws, "staging", "check", SUBJECT_A)
    assert check.returncode == 1
    assert trust_result(check, "csa-staging-deploy") == "differs"

    # And apply turns it back into the plain string.
    assert with_subjects(aws, "staging", "apply", SUBJECT_A).returncode == 0
    assert stored_sub(aws, "csa-staging-deploy") == f"{SUBJECT_A}:environment:staging"
    # A stored string against two given subjects is a difference too.
    assert with_subjects(aws, "staging", "check", SUBJECT_A, SUBJECT_B).returncode == 1


@pytest.mark.parametrize("target", ["production", "staging"])
def test_apply_twice_with_several_subjects_makes_no_write(aws: Aws, target: str) -> None:
    subjects = (SUBJECT_A, SUBJECT_B, SUBJECT_C)
    assert with_subjects(aws, target, "apply", *subjects).returncode == 0
    state_after_first = aws.state()
    aws.reset_log()

    second = with_subjects(aws, target, "apply", *subjects)

    assert second.returncode == 0, second.stderr
    assert aws.commands()
    assert not [c for c in aws.commands() if CREATE_OR_UPDATE.match(c)]
    assert aws.state() == state_after_first
    assert not {m.group(4) for m in step_lines(second)} & {"created", "updated"}


def test_apply_leaves_a_live_list_in_another_order_alone_when_the_set_is_equal(aws: Aws) -> None:
    # Decision: the order stored in AWS is not significant, so apply does not rewrite it.
    subjects = (SUBJECT_A, SUBJECT_B, SUBJECT_C)
    assert with_subjects(aws, "production", "apply", *subjects).returncode == 0
    reordered = list(reversed(expected_list("csa-prod-deploy-plan", *subjects)))
    set_stored_sub(aws, "csa-prod-deploy-plan", reordered)
    aws.reset_log()

    result = with_subjects(aws, "production", "apply", *subjects)

    assert result.returncode == 0, result.stderr
    assert not [c for c in aws.commands() if CREATE_OR_UPDATE.match(c)]
    assert stored_sub(aws, "csa-prod-deploy-plan") == reordered
    assert trust_result(result, "csa-prod-deploy-plan") == "unchanged"


def test_apply_updates_the_trust_when_the_set_differs_and_writes_the_given_order(
    aws: Aws,
) -> None:
    assert with_subjects(aws, "production", "apply", SUBJECT_A).returncode == 0
    aws.reset_log()

    result = with_subjects(aws, "production", "apply", SUBJECT_C, SUBJECT_A, SUBJECT_B)

    assert result.returncode == 0, result.stderr
    assert [c for c in aws.commands() if CREATE_OR_UPDATE.match(c)] == [
        "iam update-assume-role-policy",
        "iam update-assume-role-policy",
    ]
    for role in TARGETS["production"]["roles"]:
        assert stored_sub(aws, role) == expected_list(role, SUBJECT_C, SUBJECT_A, SUBJECT_B)
        assert trust_result(result, role) == "updated"
    # Shrinking back to one subject turns the list into a string again; that removes two live
    # subjects, so it needs --allow-subject-removal (see the removal guard tests below).
    assert with_subjects(aws, "production", "apply", SUBJECT_A, allow_removal=True).returncode == 0
    assert stored_sub(aws, "csa-prod-deploy-plan") == f"{SUBJECT_A}:environment:production-plan"


def test_the_subject_list_is_built_with_jq_and_keeps_each_roles_environment(aws: Aws) -> None:
    code = "\n".join(
        line for line in SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    assert "--args" in code
    assert with_subjects(aws, "production", "apply", SUBJECT_A, SUBJECT_B).returncode == 0
    assert stored_sub(aws, "csa-prod-deploy-plan") == [
        f"{SUBJECT_A}:environment:production-plan",
        f"{SUBJECT_B}:environment:production-plan",
    ]
    assert stored_sub(aws, "csa-prod-deploy-apply") == [
        f"{SUBJECT_A}:environment:production",
        f"{SUBJECT_B}:environment:production",
    ]


SEVERAL_SUBJECTS_SENTENCE = (
    "production's live roles trust several subjects, so `production check` must be given all "
    "of them (and `production apply` is never run with fewer)"
)


def test_the_header_and_the_runbooks_say_that_subject_may_be_repeated() -> None:
    header = SCRIPT.read_text().split("set -euo pipefail")[0]
    assert (
        "may be repeated: pass every subject the role must trust, including the subjects of a "
        "planned repository or organisation move"
    ) in " ".join(header.replace("#", " ").split())
    staging_doc = " ".join((ROOT / "deploy" / "STAGING_PIPELINE.md").read_text().split())
    production_doc = " ".join(PRODUCTION_DOC.read_text().split())
    assert SEVERAL_SUBJECTS_SENTENCE in staging_doc.lower()
    assert SEVERAL_SUBJECTS_SENTENCE in production_doc.lower()


# --- apply refuses to remove subjects a live role trusts ------------------------------------

REFUSAL_HINT = (
    "give them with --subject, or pass --allow-subject-removal if the removal is intended"
)
THREE = (SUBJECT_A, SUBJECT_B, SUBJECT_C)
SUBJECT_D = "repo:example-org@4004/CSAV2@2002"


def listed(role: str, *subjects: str) -> str:
    return ", ".join(expected_list(role, *subjects))


def refusal(role: str, count: int, *subjects: str) -> str:
    return f"refused: role {role} trusts {count} subject(s) not given: {listed(role, *subjects)}"


def removal_note(role: str, count: int, *subjects: str) -> str:
    return f"removing {count} subject(s) from role {role} trust: {listed(role, *subjects)}"


def adding_note(role: str, count: int, *subjects: str) -> str:
    return f"adding {count} subject(s) to role {role} trust: {listed(role, *subjects)}"


def writes(aws: Aws) -> list[str]:
    return [c for c in aws.commands() if not READ_ONLY.match(c)]


def set_trust(aws: Aws, role: str, trust: Any) -> None:
    state = aws.state()
    state["roles"][role]["trust"] = trust
    aws.save(state)


@pytest.mark.parametrize("target", ["production", "staging"])
def test_apply_refuses_to_remove_live_subjects_and_writes_nothing(aws: Aws, target: str) -> None:
    assert with_subjects(aws, target, "apply", *THREE).returncode == 0
    before = aws.state()
    aws.reset_log()

    result = with_subjects(aws, target, "apply", SUBJECT_A)

    assert result.returncode == 1
    expected = [refusal(role, 2, SUBJECT_B, SUBJECT_C) for role in TARGETS[target]["roles"]]
    assert result.stderr.splitlines() == [*expected, REFUSAL_HINT]
    assert MARKER not in result.stdout + result.stderr
    # The refusal comes before step 1: the existing apply note, and no step line.
    assert step_lines(result) == []
    assert [line for line in result.stdout.splitlines() if line] == [
        next(
            line for line in result.stdout.splitlines() if line.startswith("apply note: run check")
        )
    ]
    assert aws.state() == before
    assert aws.commands() and writes(aws) == []
    assert "iam create-open-id-connect-provider" not in aws.commands()


def test_apply_with_the_flag_removes_the_subjects_and_says_so_before_the_steps(aws: Aws) -> None:
    assert with_subjects(aws, "production", "apply", *THREE).returncode == 0
    aws.reset_log()

    result = with_subjects(aws, "production", "apply", SUBJECT_A, allow_removal=True)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    plan, apply_ = "csa-prod-deploy-plan", "csa-prod-deploy-apply"
    notes = [
        removal_note(plan, 2, SUBJECT_B, SUBJECT_C),
        removal_note(apply_, 2, SUBJECT_B, SUBJECT_C),
    ]
    assert all(note in lines for note in notes)
    first_step = next(i for i, line in enumerate(lines) if STEP_LINE.match(line))
    assert max(lines.index(note) for note in notes) < first_step
    assert not [line for line in lines if line.startswith("adding ")]
    for role in (plan, apply_):
        assert stored_sub(aws, role) == f"{SUBJECT_A}:environment:{ENVIRONMENTS[role]}"
        assert trust_result(result, role) == "updated"
    assert result.stderr == ""


def test_a_swap_is_a_removal_and_is_refused(aws: Aws) -> None:
    assert with_subjects(aws, "production", "apply", *THREE).returncode == 0
    before = aws.state()
    aws.reset_log()

    result = with_subjects(aws, "production", "apply", SUBJECT_A, SUBJECT_B, SUBJECT_D)

    assert result.returncode == 1
    assert result.stderr.splitlines() == [
        refusal("csa-prod-deploy-plan", 1, SUBJECT_C),
        refusal("csa-prod-deploy-apply", 1, SUBJECT_C),
        REFUSAL_HINT,
    ]
    assert aws.state() == before
    assert writes(aws) == []

    # With the flag the swap goes through, and both the removal and the addition are announced.
    swapped = with_subjects(
        aws, "production", "apply", SUBJECT_A, SUBJECT_B, SUBJECT_D, allow_removal=True
    )
    assert swapped.returncode == 0, swapped.stderr
    for role in TARGETS["production"]["roles"]:
        assert removal_note(role, 1, SUBJECT_C) in swapped.stdout.splitlines()
        assert adding_note(role, 1, SUBJECT_D) in swapped.stdout.splitlines()
        assert stored_sub(aws, role) == expected_list(role, SUBJECT_A, SUBJECT_B, SUBJECT_D)


def test_growing_from_one_subject_to_three_is_allowed_and_says_adding(aws: Aws) -> None:
    assert with_subjects(aws, "production", "apply", SUBJECT_A).returncode == 0
    aws.reset_log()

    result = with_subjects(aws, "production", "apply", *THREE)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    for role in TARGETS["production"]["roles"]:
        assert adding_note(role, 2, SUBJECT_B, SUBJECT_C) in lines
        assert stored_sub(aws, role) == expected_list(role, *THREE)
    assert not [line for line in lines if line.startswith("removing ")]
    assert result.stderr == ""


@pytest.mark.parametrize(
    "live_as_string",
    [False, True],
    ids=["list-reordered", "string-equals-one-element-list"],
)
def test_set_equal_trusts_are_not_a_removal(aws: Aws, live_as_string: bool) -> None:
    given = (SUBJECT_A,) if live_as_string else THREE
    assert with_subjects(aws, "production", "apply", *given).returncode == 0
    for role in TARGETS["production"]["roles"]:
        live = expected_list(role, *given)
        set_stored_sub(aws, role, live if live_as_string else list(reversed(live)))
    aws.reset_log()

    result = with_subjects(aws, "production", "apply", *given)

    assert result.returncode == 0, result.stderr
    assert not [
        line for line in result.stdout.splitlines() if line.startswith(("removing", "adding"))
    ]
    if live_as_string:
        # A one-element list is not the plain string the template gives: that is a rewrite
        # (an update), but never a removal.
        assert writes(aws) == ["iam update-assume-role-policy"] * 2
    else:
        assert writes(aws) == []


def test_a_live_list_with_a_repeated_entry_is_the_same_set(aws: Aws) -> None:
    assert with_subjects(aws, "staging", "apply", SUBJECT_A, SUBJECT_B).returncode == 0
    role = "csa-staging-deploy"
    live = expected_list(role, SUBJECT_A, SUBJECT_B)
    set_stored_sub(aws, role, [live[1], live[0], live[1]])
    aws.reset_log()

    assert with_subjects(aws, "staging", "check", SUBJECT_A, SUBJECT_B).returncode == 0
    result = with_subjects(aws, "staging", "apply", SUBJECT_A, SUBJECT_B)

    assert result.returncode == 0, result.stderr
    assert writes(aws) == []


def test_the_guard_runs_before_any_write_so_a_later_role_cannot_leave_a_half_apply(
    aws: Aws,
) -> None:
    # The plan role (updated first) would only grow; the apply role (second) would shrink.
    # Nothing, not even the plan role or the missing provider, may be written.
    assert with_subjects(aws, "production", "apply", SUBJECT_A).returncode == 0
    apply_role = "csa-prod-deploy-apply"
    set_stored_sub(aws, apply_role, expected_list(apply_role, *THREE))
    state = aws.state()
    state["providers"] = []
    aws.save(state)
    before = aws.state()
    aws.reset_log()

    result = with_subjects(aws, "production", "apply", SUBJECT_A, SUBJECT_B)

    assert result.returncode == 1
    assert result.stderr.splitlines() == [refusal(apply_role, 1, SUBJECT_C), REFUSAL_HINT]
    assert aws.state() == before
    assert writes(aws) == []
    assert stored_sub(aws, "csa-prod-deploy-plan") == f"{SUBJECT_A}:environment:production-plan"
    # And with the flag everything goes through, the plan role growing and the apply role shrinking.
    done = with_subjects(aws, "production", "apply", SUBJECT_A, SUBJECT_B, allow_removal=True)
    assert done.returncode == 0, done.stderr
    assert stored_sub(aws, apply_role) == expected_list(apply_role, SUBJECT_A, SUBJECT_B)
    assert aws.state()["providers"] == [OIDC_ARN]


def test_roles_that_do_not_exist_yet_are_not_part_of_the_guard(aws: Aws) -> None:
    # No live role at all (a first apply), then only the plan role live and growing.
    first = with_subjects(aws, "production", "apply", *THREE)
    assert first.returncode == 0, first.stderr
    assert not [
        line for line in first.stdout.splitlines() if line.startswith(("removing", "adding"))
    ]

    state = aws.state()
    del state["roles"]["csa-prod-deploy-apply"]
    aws.save(state)
    aws.reset_log()
    second = with_subjects(aws, "production", "apply", SUBJECT_A, SUBJECT_B)
    assert second.returncode == 1  # the plan role still trusts three subjects
    assert second.stderr.count("refused:") == 1
    assert "csa-prod-deploy-apply" not in second.stderr


def test_the_guard_reads_every_statement_and_counts_strings_and_lists(aws: Aws) -> None:
    assert with_subjects(aws, "staging", "apply", SUBJECT_A).returncode == 0
    role = "csa-staging-deploy"
    trust = aws.state()["roles"][role]["trust"]
    [first] = trust["Statement"]
    second = json.loads(json.dumps(first))
    second["Condition"]["StringEquals"][SUB_KEY] = expected_list(role, SUBJECT_B, SUBJECT_C)
    trust["Statement"] = [first, second]
    set_trust(aws, role, trust)
    aws.reset_log()

    result = with_subjects(aws, "staging", "apply", SUBJECT_A)

    assert result.returncode == 1
    assert result.stderr.splitlines() == [refusal(role, 2, SUBJECT_B, SUBJECT_C), REFUSAL_HINT]
    assert writes(aws) == []


def test_only_subject_shaped_values_are_printed_from_the_live_trust(aws: Aws) -> None:
    assert with_subjects(aws, "staging", "apply", SUBJECT_A).returncode == 0
    role = "csa-staging-deploy"
    set_stored_sub(
        aws,
        role,
        [
            f"{SUBJECT_A}:environment:staging",
            "evil\nwords $(x) FAKE-AWS-TEXT",
            SUBJECT_B + ":environment:staging",
        ],
    )
    aws.reset_log()

    result = with_subjects(aws, "staging", "apply", SUBJECT_A)

    assert result.returncode == 1
    assert result.stderr.splitlines() == [
        f"refused: role {role} trusts 2 subject(s) not given: "
        f"<unrecognised value>, {SUBJECT_B}:environment:staging",
        REFUSAL_HINT,
    ]
    assert "FAKE-AWS-TEXT" not in result.stdout + result.stderr
    assert writes(aws) == []


STAGING_ROLE = "csa-staging-deploy"


def stage_live_trust(aws: Aws, statements: list[dict[str, Any]]) -> None:
    """Applies subject A to staging, then replaces the live trust's statements."""
    assert with_subjects(aws, "staging", "apply", SUBJECT_A).returncode == 0
    trust = aws.state()["roles"][STAGING_ROLE]["trust"]
    trust["Statement"] = statements
    set_trust(aws, STAGING_ROLE, trust)
    aws.reset_log()


def live_statement(condition: dict[str, Any], action: str = "sts:AssumeRoleWithWebIdentity") -> Any:
    return {
        "Effect": "Allow",
        "Principal": {"Federated": OIDC_ARN},
        "Action": action,
        "Condition": condition,
    }


def assert_staging_refusal_for_b(aws: Aws) -> None:
    result = with_subjects(aws, "staging", "apply", SUBJECT_A)
    assert result.returncode == 1
    assert result.stderr.splitlines() == [
        refusal(STAGING_ROLE, 1, SUBJECT_B),
        REFUSAL_HINT,
    ]
    assert writes(aws) == []


@pytest.mark.parametrize(
    "operator", ["StringLike", "ForAnyValue:StringLike", "ForAllValues:StringEquals"]
)
def test_the_guard_reads_the_sub_under_any_condition_operator(aws: Aws, operator: str) -> None:
    both = [f"{SUBJECT_A}:environment:staging", f"{SUBJECT_B}:environment:staging"]
    stage_live_trust(aws, [live_statement({operator: {SUB_KEY: both}})])

    assert_staging_refusal_for_b(aws)


def test_the_guard_reads_a_second_statement_with_another_action(aws: Aws) -> None:
    mine = {"StringEquals": {SUB_KEY: f"{SUBJECT_A}:environment:staging"}}
    other = {"StringLike": {SUB_KEY: f"{SUBJECT_B}:environment:staging"}}
    stage_live_trust(aws, [live_statement(mine), live_statement(other, action="sts:TagSession")])

    assert_staging_refusal_for_b(aws)


def test_the_guard_reads_the_sub_key_in_any_letter_case(aws: Aws) -> None:
    # IAM condition keys are case-insensitive: a live trust may spell the key differently.
    key = "Token.Actions.GithubUsercontent.Com:Sub"
    both = [f"{SUBJECT_A}:environment:staging", f"{SUBJECT_B}:environment:staging"]
    stage_live_trust(aws, [live_statement({"StringEquals": {key: both}})])

    assert_staging_refusal_for_b(aws)


@pytest.mark.parametrize(
    "value",
    [
        f"{SUBJECT_B}:environment:staging\n",
        f"FAKE-AWS-TEXT\n{SUBJECT_B}:environment:staging",
        f"{SUBJECT_B}:environment:staging\nFAKE-AWS-TEXT",
    ],
    ids=["trailing-newline", "leading-line", "trailing-line"],
)
def test_a_value_with_a_newline_is_never_shown_even_if_a_line_looks_like_a_subject(
    aws: Aws, value: str
) -> None:
    stage_live_trust(
        aws,
        [live_statement({"StringEquals": {SUB_KEY: [f"{SUBJECT_A}:environment:staging", value]}})],
    )

    result = with_subjects(aws, "staging", "apply", SUBJECT_A)

    assert result.returncode == 1
    assert result.stderr.splitlines() == [
        f"refused: role {STAGING_ROLE} trusts 1 subject(s) not given: <unrecognised value>",
        REFUSAL_HINT,
    ]
    assert "FAKE-AWS-TEXT" not in result.stdout + result.stderr
    assert writes(aws) == []


def test_apply_replaces_the_whole_trust_and_check_reports_an_extra_statement(aws: Aws) -> None:
    mine = {"StringEquals": {SUB_KEY: f"{SUBJECT_A}:environment:staging"}}
    extra = {"Effect": "Allow", "Principal": {"AWS": "arn:aws:iam::000000000000:root"}}
    stage_live_trust(aws, [live_statement(mine), {**extra, "Action": "sts:AssumeRole"}])

    check = with_subjects(aws, "staging", "check", SUBJECT_A)
    assert check.returncode == 1
    assert trust_result(check, STAGING_ROLE) == "differs"

    applied = with_subjects(aws, "staging", "apply", SUBJECT_A)
    assert applied.returncode == 0, applied.stderr
    assert len(aws.state()["roles"][STAGING_ROLE]["trust"]["Statement"]) == 1
    assert with_subjects(aws, "staging", "check", SUBJECT_A).returncode == 0


WHOLE_TRUST_SENTENCE = (
    "`apply` replaces the whole trust document of a role, not only its subject: any extra "
    "statement or condition in a live trust is overwritten, and `check` reports it as `differs`"
)


def test_the_header_and_the_production_runbook_say_apply_replaces_the_whole_trust() -> None:
    header = " ".join(SCRIPT.read_text().split("set -euo pipefail")[0].replace("#", " ").split())
    assert WHOLE_TRUST_SENTENCE in header
    assert WHOLE_TRUST_SENTENCE in " ".join(PRODUCTION_DOC.read_text().split())
    assert "in a refusal, the live subjects" not in header


def test_the_removal_flag_is_valueless_and_accepted_anywhere_and_by_every_action(aws: Aws) -> None:
    base = ["--region", REGION, "--node", NODE, "--subject", SUBJECT_A]
    for args in (
        ["--allow-subject-removal", *base],
        [*base, "--allow-subject-removal"],
        ["--region", REGION, "--allow-subject-removal", "--node", NODE, "--subject", SUBJECT_A],
        [*base, "--allow-subject-removal", "--allow-subject-removal"],
    ):
        result = aws.run("staging", "check", *args)
        assert result.returncode == 1, result.stderr  # it ran; nothing exists yet
        assert aws.calls() and all(READ_ONLY.match(c) for c in aws.commands())
        aws.reset_log()
    # activate and check take it as a no-op (the guard concerns apply only).
    assert activate(aws, "staging", "--allow-subject-removal").returncode == 0
    assert aws.calls()
    aws.reset_log()
    assert with_subjects(aws, "staging", "apply", SUBJECT_A, allow_removal=True).returncode == 0
    assert with_subjects(aws, "staging", "check", SUBJECT_A, allow_removal=True).returncode == 0


@pytest.mark.parametrize(
    "extra",
    [
        ["--allow-subject-removal=x"],
        ["--allow-subject-removal=1"],
        ["--allow-subject-removal="],
        ["--allow-subject-removal", "yes"],
        ["--subject"],
        ["--subject", "--allow-subject-removal"],
        ["--subject", "--region", REGION],
        [f"--subject={SUBJECT_B}"],
        ["--subject", "--node", NODE],
    ],
    ids=[
        "equals-x",
        "equals-1",
        "equals-empty",
        "value-given",
        "subject-last",
        "subject-then-flag",
        "subject-then-region",
        "subject-equals-form",
        "subject-then-node",
    ],
)
def test_malformed_flag_forms_are_refused_before_any_aws_call(aws: Aws, extra: list[str]) -> None:
    base = ["--region", REGION, "--node", NODE, "--subject", SUBJECT_A]
    for action in ("apply", "check"):
        result = aws.run("production", action, *base, *extra)
        assert result.returncode == 2, (extra, result.stdout, result.stderr)
        assert result.stderr
        assert aws.calls() == []


def test_the_subject_builder_fails_closed_on_an_unexpected_template_shape(
    aws: Aws, tmp_path: Path
) -> None:
    copy = tmp_path / "repo"
    (copy / "deploy" / "aws").mkdir(parents=True)
    (copy / "deploy" / "aws" / "setup-target.sh").write_bytes(SCRIPT.read_bytes())
    (copy / "deploy" / "aws" / "setup-target.sh").chmod(0o755)
    for source in (ROOT / "deploy" / "staging").rglob("*.json"):
        target = copy / "deploy" / "staging" / source.relative_to(ROOT / "deploy" / "staging")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text())
    trust = copy / "deploy" / "staging" / "iam" / "trust-policy.json"
    assert "<repo-subject>:environment:staging" in trust.read_text()
    trust.write_text(
        trust.read_text().replace(
            "<repo-subject>:environment:staging", "repo:fixed:environment:staging"
        )
    )

    result = with_subjects(
        aws,
        "staging",
        "apply",
        SUBJECT_A,
        SUBJECT_B,
        script=copy / "deploy" / "aws" / "setup-target.sh",
    )

    assert result.returncode == 1
    assert "could not put the subjects into a trust template; nothing was changed" in result.stderr
    assert writes(aws) == []
    assert list(aws.tmp.iterdir()) == []


def test_the_usage_text_and_the_header_show_that_subject_repeats(aws: Aws) -> None:
    result = aws.run("staging", "bogus")
    assert result.returncode == 2
    assert "[--subject <prefix> ...]" in result.stderr
    assert "[--allow-subject-removal]" in result.stderr
    header = " ".join(SCRIPT.read_text().split("set -euo pipefail")[0].replace("#", " ").split())
    assert "[--subject <prefix> ...]" in header
    assert "--allow-subject-removal" in header


REMOVAL_SENTENCE = (
    "`production apply` refuses to remove a subject that a live role trusts unless it is given "
    "`--allow-subject-removal`"
)
STAGING_REMOVAL_SENTENCE = (
    "`apply` refuses to remove a subject that a live role trusts unless it is given "
    "`--allow-subject-removal`"
)
MANUAL_STEP_SENTENCE = (
    "if the roles already trust several subjects, do not run the manual commands below as "
    "written (they fill one subject): use `deploy/aws/setup-target.sh production check` and "
    "`apply` with every subject instead"
)


def test_the_runbooks_document_the_removal_flag_and_the_manual_step_warning() -> None:
    staging_doc = " ".join((ROOT / "deploy" / "STAGING_PIPELINE.md").read_text().split()).lower()
    production_doc = " ".join(PRODUCTION_DOC.read_text().split()).lower()
    assert REMOVAL_SENTENCE in production_doc
    assert STAGING_REMOVAL_SENTENCE in staging_doc
    assert MANUAL_STEP_SENTENCE in production_doc


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
    # A first apply on an empty account changes no live trust, so no removing/adding line either.
    assert all(line == "" or line.startswith("apply ") for line in others), others
    notes = [line for line in others if line.startswith("apply note: ")]
    assert any("run check before apply" in line and "--subject" in line for line in notes), notes
    arn_note = [line for line in notes if "CloudShell" in line]
    assert len(arn_note) == 1 and "GitHub environment secrets" in arn_note[0], notes
    assert "account id" in arn_note[0] and arn_note[0].endswith("only"), notes
    lines = result.stdout.splitlines()
    assert lines.index(arn_note[0]) < min(
        i for i, line in enumerate(lines) if line.startswith("Role ARN ")
    )
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
