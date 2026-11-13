"""The rollout's read-only smoke test after the check files (and, later, the rollback it gates)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest
from scripts import staging_rollout as sr
from scripts.rollout_targets import STAGING, Target
from scripts.staging_rollout import Completed, RolloutError

from tests.test_rollout_production import HOST_REQUIRED, PROD
from tests.test_staging_rollout import (
    FRONTEND_REF,
    PYTHON_REF,
    RUNNING_FORMAT,
    RUNNING_REF,
    ApplyRig,
    FakeRunner,
    fail,
    has,
    labels,
    ok,
    running_json,
)

CHECKOUT = ["0484", "0485"]
CONSOLE = HOST_REQUIRED["CSA_STAGING_CONSOLE_ORIGIN"]
HOOKS = HOST_REQUIRED["CSA_STAGING_HOOKS_ORIGIN"]
ORIGINS = {
    "CSA_STAGING_CONSOLE_ORIGIN": CONSOLE,
    "CSA_STAGING_HOOKS_ORIGIN": HOOKS,
}
SMOKE_ENV = {"CSA_STAGING_PYTHON_IMAGE": PYTHON_REF, "CSA_STAGING_FRONTEND_IMAGE": FRONTEND_REF}


class SmokeHost:
    """A fake instance whose containers run the planned images; flags make one thing wrong."""

    def __init__(
        self,
        target: Target = STAGING,
        *,
        applied: Sequence[str] = CHECKOUT,
        unhealthy: Sequence[str] = (),
        stale: Sequence[str] = (),
        missing: Sequence[str] = (),
        statuses: Mapping[str, int] | None = None,
        curl_exit: int = 0,
        frontend_up: bool = True,
    ) -> None:
        self.target = target
        self.lines: list[str] = []
        ids = {}
        refs: dict[str, str] = {}
        for service in target.app_services:
            for name in sr.container_names(target, SMOKE_ENV, service):
                ids[name] = "sha256:" + f"{len(ids) + 1:064x}"
                planned = FRONTEND_REF if service == "frontend" else PYTHON_REF
                refs[ids[name]] = RUNNING_REF if name in stale else planned
        answers = {CONSOLE: 200, HOOKS: 200, **(statuses or {})}

        def health(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
            if argv[-1] in missing:
                return fail(1)
            return ok("unhealthy\n" if argv[-1] in unhealthy else "healthy\n")

        def curl(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
            if curl_exit:
                return fail(curl_exit)
            return ok(str(answers[argv[-1]]))

        self.runner = FakeRunner(
            [
                (has("{{.State.Health.Status}}"), health),
                (has("inspect --format {{.Image}}"), lambda argv, env: ok(ids[argv[-1]] + "\n")),
                (
                    lambda argv: RUNNING_FORMAT in argv,
                    lambda argv, env: ok(running_json(refs[argv[-1]])),
                ),
                (
                    lambda argv: "psql" in argv,
                    lambda argv, env: ok("".join(f"{v}\n" for v in applied)),
                ),
                (lambda argv: argv[0] == "curl", curl),
                (lambda argv: argv[3:4] == ["exec"], ok() if frontend_up else fail(1)),
            ]
        )

    def smoke(
        self,
        host_values: Mapping[str, str] | None = None,
        checkout: Sequence[str] = CHECKOUT,
    ) -> None:
        sr._run_smoke(
            self.runner,
            SMOKE_ENV,
            checkout,
            host_values or {},
            self.lines.append,
            sr._Progress(),
            target=self.target,
        )

    def stop(self, host_values: Mapping[str, str] | None = None) -> RolloutError:
        with pytest.raises(RolloutError) as caught:
            self.smoke(host_values)
        assert caught.value.step == "smoke"
        return caught.value


def test_smoke_passes_and_prints_one_line_per_check() -> None:
    host = SmokeHost(PROD)
    host.smoke(ORIGINS)
    assert host.lines == [
        "smoke migrations: ok",
        "smoke replicas: ok",
        "smoke console origin: ok",
        "smoke hooks origin: ok",
        "smoke frontend: ok",
    ]


def test_a_missing_recorded_migration_stops_at_smoke() -> None:
    host = SmokeHost(applied=["0484"])
    error = host.stop()
    assert "0485" in error.message
    assert host.lines == []
    assert error.state


def test_an_extra_recorded_migration_stops_at_smoke() -> None:
    host = SmokeHost(applied=[*CHECKOUT, "0486"])
    assert "0486" in host.stop().message


def test_an_unreadable_schema_migrations_stops_at_smoke() -> None:
    host = SmokeHost()
    host.runner.rules.insert(0, (lambda argv: "psql" in argv, fail(2)))
    assert host.stop().step == "smoke"


def test_an_unhealthy_replica_stops_at_smoke() -> None:
    host = SmokeHost(PROD, unhealthy=["csa-prod-worker-2"])
    error = host.stop(ORIGINS)
    assert "csa-prod-worker-2" in error.message
    assert host.lines == ["smoke migrations: ok"]


def test_a_replica_on_another_image_stops_at_smoke() -> None:
    host = SmokeHost(PROD, stale=["csa-prod-worker-1"])
    assert "csa-prod-worker-1" in host.stop(ORIGINS).message


def test_a_replica_that_is_gone_stops_at_smoke() -> None:
    host = SmokeHost(PROD, missing=["csa-prod-delivery-1"])
    assert "csa-prod-delivery-1" in host.stop(ORIGINS).message


def test_every_replica_of_every_app_service_is_inspected() -> None:
    host = SmokeHost(PROD)
    host.smoke(ORIGINS)
    inspected = {argv[-1] for argv in host.runner.argvs if "{{.State.Health.Status}}" in argv}
    assert inspected == {
        "csa-prod-gateway-1",
        "csa-prod-worker-1",
        "csa-prod-worker-2",
        "csa-prod-delivery-1",
        "csa-prod-frontend-1",
    }


def test_origins_are_checked_only_when_the_host_file_has_them() -> None:
    staging = SmokeHost()
    staging.smoke()
    assert not [argv for argv in staging.runner.argvs if argv[0] == "curl"]
    assert not any("origin" in line for line in staging.lines)

    one_only = SmokeHost(PROD)
    one_only.smoke({"CSA_STAGING_CONSOLE_ORIGIN": CONSOLE})
    assert not [argv for argv in one_only.runner.argvs if argv[0] == "curl"]

    production = SmokeHost(PROD)
    production.smoke(ORIGINS)
    curls = [argv for argv in production.runner.argvs if argv[0] == "curl"]
    assert [argv[-1] for argv in curls] == [CONSOLE, HOOKS]
    assert all(
        "--max-time" in argv and argv[argv.index("--max-time") + 1] == "15" for argv in curls
    )


def test_a_5xx_from_an_origin_stops_at_smoke() -> None:
    host = SmokeHost(PROD, statuses={HOOKS: 502})
    error = host.stop(ORIGINS)
    assert "hooks origin" in error.message and "502" in error.message
    assert host.lines[-1] == "smoke console origin: ok"


def test_a_4xx_or_a_redirect_from_an_origin_still_passes() -> None:
    host = SmokeHost(PROD, statuses={CONSOLE: 404, HOOKS: 302})
    host.smoke(ORIGINS)
    assert "smoke hooks origin: ok" in host.lines


def test_an_origin_that_does_not_answer_stops_at_smoke() -> None:
    host = SmokeHost(PROD, curl_exit=28)
    assert "console origin" in host.stop(ORIGINS).message


def test_an_origin_value_is_never_printed() -> None:
    for host in (SmokeHost(PROD, statuses={CONSOLE: 500}), SmokeHost(PROD, curl_exit=6)):
        error = host.stop(ORIGINS)
        shown = " ".join([error.message, error.state or "", *host.lines])
        assert CONSOLE not in shown and HOOKS not in shown
        assert "example.test" not in shown


def test_the_frontend_is_probed_over_http_from_the_gateway() -> None:
    host = SmokeHost()
    host.smoke()
    probes = [argv for argv in host.runner.argvs if argv[3:4] == ["exec"]]
    assert len(probes) == 1
    assert probes[0][:7] == [*sr.docker_base(), "exec", "csa-staging-gateway-1", "python", "-c"]
    assert probes[0][-1] == sr.FRONTEND_PROBE
    assert "http://frontend:3000" in sr.FRONTEND_PROBE


def test_a_frontend_that_does_not_answer_stops_at_smoke() -> None:
    host = SmokeHost(frontend_up=False)
    error = host.stop()
    assert "frontend" in error.message
    assert host.lines[-1] == "smoke replicas: ok"


def test_smoke_makes_no_write_call() -> None:
    host = SmokeHost(PROD)
    host.smoke(ORIGINS)
    for argv in host.runner.argvs:
        joined = f" {' '.join(argv)} "
        for verb in (" up ", " stop ", " start ", " restart ", " run ", " rm ", " kill ", " pull "):
            assert verb not in joined, joined
        if "psql" in argv:
            assert "-f" not in argv
            statement = argv[argv.index("-c") + 1]
            assert statement.lstrip().upper().startswith("SELECT")
            assert ";" not in statement.rstrip(";")
        elif argv[0] == "docker":
            assert argv[3] in {"inspect", "image", "exec"}
            if argv[3] == "exec":
                assert argv[4:7] == ["csa-prod-gateway-1", "python", "-c"]
        else:
            assert argv[0] == "curl"


# --- inside apply ------------------------------------------------------------------------


@pytest.fixture
def rig_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., ApplyRig]:
    checks_dir = tmp_path / "checks"
    checks_dir.mkdir()
    (checks_dir / "0485.sql").write_text("SELECT 0;\n")
    secret_dir = tmp_path / "secrets"
    monkeypatch.setattr(sr, "SECRET_DIR", secret_dir)
    monkeypatch.setattr(sr, "CHECKS_DIR", checks_dir)
    return lambda **kwargs: ApplyRig(tmp_path, secret_dir, monkeypatch, **kwargs)


def test_apply_stops_at_smoke_with_the_new_images_running(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(overrides=[(has("{{.State.Health.Status}}"), ok("unhealthy\n"))])
    assert rig.apply() == 1
    assert any(line.startswith("Stopped at smoke: ") for line in rig.lines)
    assert not any(line.startswith("Rollout of ") for line in rig.lines)
    assert "services are up on the new images" in rig.text
    assert "smoke migrations: ok" in rig.lines
    assert "ps" not in labels(rig.runner)
    assert "stop" not in labels(rig.runner)


def test_apply_prints_the_smoke_lines_before_the_final_status(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory()
    assert rig.apply() == 0
    smoke = [line for line in rig.lines if line.startswith("smoke ")]
    assert smoke == ["smoke migrations: ok", "smoke replicas: ok", "smoke frontend: ok"]
    status = next(i for i, line in enumerate(rig.lines) if line.startswith("gateway Up"))
    assert rig.lines.index("smoke frontend: ok") < status
    assert not any(line.startswith("Stopped") for line in rig.lines)
