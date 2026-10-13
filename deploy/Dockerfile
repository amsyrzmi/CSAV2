# CSA application image (LG01 §9.1). ONE image serves all three services
# (gateway, worker, delivery) — they differ only by `command` in compose.yml.
#
# Build with uv for a frozen, reproducible dependency closure, then run as a
# non-root user with no build toolchain in the final layer.
FROM python:3.12-slim-bookworm AS build

# Pin uv by copying its static binary from the official distroless image.
COPY --from=ghcr.io/astral-sh/uv:0.9.6 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies first (cached) from the locked, frozen set — no dev deps.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Add the project source and install the project itself.
COPY apps ./apps
COPY csa ./csa
COPY scripts ./scripts
COPY db ./db
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime

# Create a non-root runtime user (matches the stack's 1000:1000 convention).
RUN groupadd --gid 1000 csa \
 && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin csa

WORKDIR /app

# Copy the resolved virtualenv and the application source from the build stage.
COPY --from=build --chown=1000:1000 /app /app

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 1000:1000

# Default to the gateway; compose overrides `command` for worker and delivery.
EXPOSE 8080
CMD ["uvicorn", "apps.gateway.main:app", "--host", "0.0.0.0", "--port", "8080"]
