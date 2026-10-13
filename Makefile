# CSA developer Makefile (LG01 §9.3).
#
# Dev values come from the ignored .env.local. They apply ONLY to the throwaway
# csa_dev_db container (compose project csa-dev), never to production csa_db.
-include .env.local

DEV_PGHOST ?= 127.0.0.1
DEV_PGPORT ?= 5433
DEV_OWNER_PASSWORD ?= $(if $(strip $(CSA_OWNER_PASSWORD)),$(CSA_OWNER_PASSWORD),dev_owner_pw)
DEV_APP_PASSWORD ?= $(if $(strip $(CSA_APP_PASSWORD)),$(CSA_APP_PASSWORD),dev_app_pw)
DEV_RO_PASSWORD ?= $(if $(strip $(CSA_RO_PASSWORD)),$(CSA_RO_PASSWORD),dev_ro_pw)

DEV_OWNER_DSN := postgres://csa:$(DEV_OWNER_PASSWORD)@$(DEV_PGHOST):$(DEV_PGPORT)/csa
DEV_APP_DSN := postgres://csa_app:$(DEV_APP_PASSWORD)@$(DEV_PGHOST):$(DEV_PGPORT)/csa

COMPOSE_DEV := deploy/compose.dev.yml

# Use the host psql when installed; otherwise run psql inside the dev container.
# Files are always fed on stdin so both paths behave identically.
PSQL := $(if $(shell command -v psql 2>/dev/null),psql "$(DEV_OWNER_DSN)",docker exec -i csa_dev_db psql -U csa -d csa)

DEV_GATEWAY_PORT ?= 18080

.PHONY: db-image db-reset test coverage load-test lint run-dev setup-dev-telegram up down
export CHANNEL_MODE

## db-image: build the PostGIS+pgvector image used by the dev DB and the tests
db-image:
	docker build -f deploy/db.Dockerfile -t csa/pg16-postgis-vector:1 deploy

## db-reset: drop + recreate the dev DB (baseline -> migrations -> seed -> lg_setup)
db-reset:
	@echo ">> recreating dev database (public + lg schemas)"
	$(PSQL) -v ON_ERROR_STOP=1 -c "DROP SCHEMA IF EXISTS lg CASCADE;"
	$(PSQL) -v ON_ERROR_STOP=1 -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
	@echo ">> applying schema baseline"
	$(PSQL) -v ON_ERROR_STOP=1 < db/schema_baseline.sql
	@echo ">> applying migrations + lg checkpoint setup"
	@MIGRATE_DATABASE_URL="$(DEV_OWNER_DSN)" \
	LG_SETUP_DATABASE_URL="$(DEV_OWNER_DSN)" \
	CSA_APP_PASSWORD="$(DEV_APP_PASSWORD)" \
	CSA_RO_PASSWORD="$(DEV_RO_PASSWORD)" \
	    uv run python -m scripts.migrate
	LG_SETUP_DATABASE_URL="$(DEV_OWNER_DSN)" uv run python -m scripts.lg_setup
	@echo ">> applying seed data (empty in LG01)"
	@for f in db/seed/*.sql; do \
	    [ -e "$$f" ] || continue; \
	    echo ">> seed $$f"; \
	    $(PSQL) -v ON_ERROR_STOP=1 < "$$f"; \
	done
	@echo ">> db-reset complete"

## test: run the test suite (testcontainers spin their own PG; no dev DB needed)
test:
	uv run pytest

## coverage: policy branch coverage (timing benchmarks run separately without instrumentation)
coverage:
	ENV=ci uv run coverage run --branch --source=csa.graph.policy -m pytest -q \
	    tests/test_gate_policy.py tests/test_policy_edges.py tests/test_coordinator.py \
	    tests/test_coordinator_acceptance.py tests/test_golden.py \
	    -k "not orchestration_overhead and not parallel_wall_time"
	uv run coverage report -m --fail-under=90

## load-test: real SQL ingress + durable graph/outbox, synthetic traffic only
load-test:
	ENV=ci uv run pytest -q -s tests/test_load_acceptance.py

## lint: ruff check + ruff format --check + pyright + import-linter
lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright
	uv run lint-imports

## run-dev: start gateway + worker + delivery on the host against the dev DB
# Polling mode deletes the dev bot webhook without dropping pending updates.
run-dev:
	@test -f .env.local || { echo "Missing .env.local; copy .env.example and fill local values."; exit 1; }
	@echo ">> starting gateway (127.0.0.1:$(DEV_GATEWAY_PORT)), worker, delivery against the dev DB (Ctrl-C to stop)"
	ENV=dev sh -c 'uv run uvicorn apps.gateway.main:app --host 127.0.0.1 --port $(DEV_GATEWAY_PORT) & \
	           uv run python -m apps.worker.main & \
	           uv run python -m apps.delivery.main & \
	           if [ "$$CHANNEL_MODE" = "polling" ]; then uv run python -m apps.telegram_poller.main & fi; \
	           wait'

## setup-dev-telegram: store a dev bot credential in the local DB and print its webhook URL
setup-dev-telegram:
	@test -f .env.local || { echo "Missing .env.local; copy .env.example and fill local values."; exit 1; }
	ENV=dev uv run python -m scripts.setup_dev_telegram

## up / down: bring the dev Postgres up or down
up:
	@test -f .env.local || { echo "Missing .env.local; copy .env.example and fill local values."; exit 1; }
	docker compose --env-file .env.local -f $(COMPOSE_DEV) up -d

down:
	docker compose -f $(COMPOSE_DEV) down
