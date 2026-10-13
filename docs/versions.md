# Resolved dependency versions

Authoritative record of the EXACT pins resolved by `uv` for the LG01 foundation.
The source of truth is `uv.lock` + `pyproject.toml` (exact `==` pins, no ranges);
this file is the human-readable summary.

- uv: 0.13.0
- Python: 3.12 (`requires-python = ">=3.12,<3.13"`)
- Resolved: 2026-10-11

## Critical pins (read from uv.lock, never copied from the design)

| Package | Version |
|---|---|
| langgraph | 1.2.14 |
| langgraph-checkpoint-postgres | 3.1.2 |
| langgraph-checkpoint (transitive) | 4.2.0 |

## Direct runtime dependencies

| Package | Version |
|---|---|
| cryptography | 50.0.2 |
| fastapi | 0.143.0 |
| httpx | 0.28.1 |
| jsonschema | 4.26.0 |
| langchain-core | 1.6.9 |
| langchain-groq | 1.1.3 |
| langgraph | 1.2.14 |
| langgraph-checkpoint-postgres | 3.1.2 |
| opentelemetry-api | 1.45.1 |
| opentelemetry-exporter-otlp-proto-grpc | 1.45.1 |
| opentelemetry-instrumentation-fastapi | 0.66b1 |
| opentelemetry-sdk | 1.45.1 |
| orjson | 3.13.0 |
| psycopg[binary,pool] | 3.3.6 |
| pydantic | 2.14.0 |
| pydantic-settings | 2.15.0 |
| structlog | 26.1.0 |
| tenacity | 9.2.1 |
| uvicorn[standard] | 0.54.0 |

## Direct dev dependencies

| Package | Version |
|---|---|
| import-linter | 2.15 |
| pyright | 1.1.414 |
| pytest | 9.1.1 |
| pytest-asyncio | 1.4.0 |
| ruff | 0.17.0 |
| testcontainers[postgres] | 4.15.0 |

## Combined PostGIS + pgvector DB image (FEAT-004)

Dev (`deploy/compose.dev.yml`) and CI (`.github/workflows/ci.yml`) pin ONE
combined image built from `deploy/db.Dockerfile` (`pgvector/pgvector:pg16` +
`postgresql-16-postgis-3`) so the extension set cannot drift between dev and
CI.

| Field | Value |
|---|---|
| Build source | `deploy/db.Dockerfile` |
| Registry path | `ghcr.io/amsyrzmi/csa-pg16-postgis-vector` |
| Content digest | `sha256:a61cb35736e5792140f139827bc86c698d17675f617cb8c46218e15e5085b223` |

The dev compose file and CI pin the combined image by digest. The image must be
published and readable by the dev and CI before those environments can pull
it; verify registry availability rather than assuming a local build is
published.

**Server reproducibility risk (design §12.3):** the existing server `csa_db`
uses a stock `pgvector/pgvector:pg16` image with PostGIS installed separately
in the running container. That setup is not reproducible from the stock image
tag. Migrating the server database to the committed combined PostGIS+pgvector
image is a deployment prerequisite; do not perform it as part of dev
development or LG01 validation.

## Notes

- `typesafe-sdk` is intentionally ABSENT in LG01 (deferred to LG06 per design §12.4).
- `cryptography` is added for AES-256-GCM envelope encryption (design §6).
