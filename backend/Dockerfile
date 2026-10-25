# syntax=docker/dockerfile:1

# CandleStack backend. The dependencies are installed with uv into a venv on the same Python
# image the app runs on. The runtime image gets that venv and the app's source as separate
# layers, so a new commit ships only the small source layer and the dependency layer is pulled
# again only when uv.lock changes. Build context: backend/.
#   docker build -t candlestack-backend --build-arg APP_COMMIT=$(git rev-parse HEAD) backend
# The worker that runs the models is the stage `worker` of the same file:
#   docker build -t candlestack-worker --target worker backend

FROM ghcr.io/astral-sh/uv:0.12 AS uv

# The dependencies alone: this stage is reused until pyproject.toml or uv.lock change. No
# dependency group: the dev tools and the worker's Keras and JAX stay out of the app's image.
FROM python:3.13-slim AS deps
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-default-groups --no-install-project

# Local development only (compose.yaml): dev dependencies (not the worker's), the project
# installed editable so the mounted src/ is what runs, granian with --reload.
FROM deps AS dev
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-group worker
COPY granian-logging.json ./
COPY migrations ./migrations
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
# The jobs' files, a volume shared with the worker, which runs as 65534: a new named volume
# takes this directory's owner.
RUN mkdir -p /data/jobs && chown 65534:65534 /data/jobs
EXPOSE 8000
CMD ["granian", "--interface", "asgi", "--host", "0.0.0.0", "--port", "8000", \
     "--no-access-log", "--log-config", "granian-logging.json", "--reload", \
     "--reload-paths", "src", "candlestack.main:app"]

# The worker's dependencies: the app's, and the worker group (Keras, JAX).
FROM deps AS worker-deps
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-default-groups --group worker --no-install-project

# The worker that runs the models users upload (python -m candlestack.worker). Hardened like the
# app below: no pip, unprivileged, and it writes only to the jobs' volume and to /tmp, where
# Keras keeps its settings (KERAS_HOME) and the copy of the model it loads.
FROM python:3.13-slim AS worker
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade --yes \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip uninstall --yes --quiet --root-user-action=ignore pip
WORKDIR /app
COPY --from=worker-deps /app/.venv /app/.venv
COPY src ./src
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MALLOC_ARENA_MAX=2 \
    KERAS_BACKEND=jax \
    KERAS_HOME=/tmp/keras \
    JAX_PLATFORMS=cpu
# The jobs' files (JOB_FILES_DIR), a volume shared with the backend; a new named volume takes
# this directory's owner.
RUN mkdir -p /data/jobs && chown 65534:65534 /data/jobs
USER 65534:65534
ARG APP_COMMIT=unknown
ENV APP_COMMIT=$APP_COMMIT
CMD ["python", "-m", "candlestack.worker"]

# Keep last: build.yml (without a target), the CI e2e build and tests/e2e/compose.yaml build the
# last stage.
# The app runs from /app/src on PYTHONPATH, next to the dependencies' venv; the same image runs
# `alembic upgrade head` (the migrate service), which reads [tool.alembic] in pyproject.toml and
# the migrations next to it. MALLOC_ARENA_MAX:
# glibc gives each thread that allocates (the workers that parse and resample candles) arenas of
# its own that keep freed memory; two arenas keep the process within its memory limit.
FROM python:3.13-slim AS api
# Debian's security fixes that the base image does not have yet (the image scan fails on them
# until python:3.13-slim is rebuilt with them); with an up-to-date base image it changes nothing.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade --yes \
    && rm -rf /var/lib/apt/lists/*
# The app runs from the venv and never installs anything: pip goes, with the libraries it
# bundles, which the image scan would report.
RUN python -m pip uninstall --yes --quiet --root-user-action=ignore pip
WORKDIR /app
COPY --from=deps /app/.venv /app/.venv
COPY granian-logging.json pyproject.toml ./
COPY migrations ./migrations
COPY src ./src
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MALLOC_ARENA_MAX=2
# The uploaded model files (MODEL_STORAGE_DIR) and the jobs' files shared with the worker
# (JOB_FILES_DIR) go to volumes mounted here; a new named volume takes its directory's owner, so
# the app can write to it.
RUN mkdir -p /data/models /data/jobs && chown 65534:65534 /data/models /data/jobs
# Runs unprivileged and writes only to those volumes: fine with a read-only root filesystem.
USER 65534:65534
EXPOSE 8000
ARG APP_COMMIT=unknown
ENV APP_COMMIT=$APP_COMMIT
CMD ["granian", "--interface", "asgi", "--host", "0.0.0.0", "--port", "8000", \
     "--no-access-log", "--log-config", "granian-logging.json", "candlestack.main:app"]
