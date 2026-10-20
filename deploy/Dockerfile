# CSA application image (LG01 §9.1). ONE image serves all three services
# (gateway, worker, delivery) — they differ only by `command` in compose.yml.
#
# Build with uv for a frozen, reproducible dependency closure, then run as a
# non-root user with no build toolchain in the final layer.
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS build

# Pin uv by copying its static binary from the official distroless image.
COPY --from=ghcr.io/astral-sh/uv:0.9.6@sha256:4b96ee9429583983fd172c33a02ecac5242d63fb46bc27804748e38c1cc9ad0d /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies first (cached) from the locked, frozen set — no dev deps.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev --no-cache

# Run source from /app; avoid an unnecessary, unlocked build-backend installation.
COPY apps ./apps
COPY csa ./csa
COPY scripts ./scripts
COPY db ./db

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS runtime
ARG SOURCE_REVISION
ARG SOURCE_DATE_EPOCH
RUN printf '%s' "$SOURCE_REVISION" | grep -Eq '^[0-9a-f]{40}$'
LABEL org.opencontainers.image.revision=$SOURCE_REVISION

# Create a non-root runtime user (matches the stack's 1000:1000 convention).
RUN groupadd --gid 1000 csa \
 && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin csa

WORKDIR /app

# Copy the resolved virtualenv and the application source from the build stage.
COPY --from=build --chown=1000:1000 /app /app

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NO_LOCAL_ENV=1

USER 1000:1000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=2).close()"]

# Default to the gateway; compose overrides `command` for worker and delivery.
EXPOSE 8080
CMD ["uvicorn", "apps.gateway.main:app", "--host", "0.0.0.0", "--port", "8080"]
