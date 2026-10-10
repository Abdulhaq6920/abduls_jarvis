# syntax=docker/dockerfile:1.7
# JARVIS backend image (API, worker and migrate all use this one image).
#
# Build from the repository root:   docker build -f docker/Dockerfile -t jarvis-backend .
#
# Security properties of the result:
#   * multi-stage: compilers and pip caches never reach the runtime image
#   * runs as an unprivileged user (uid 10001), no shell login, no home directory writes
#   * dependencies installed from the pinned constraints file (reproducible, auditable)
#   * no secrets are copied in (.dockerignore excludes .env); they arrive at runtime
#   * the container filesystem can be mounted read-only (compose does this)

ARG PYTHON_VERSION=3.11

# ---------------------------------------------------------------- build stage
FROM python:${PYTHON_VERSION}-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /src
COPY constraints.txt ./
COPY jarvis-shared ./jarvis-shared
COPY jarvis-backend ./jarvis-backend
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install -c constraints.txt ./jarvis-shared "./jarvis-backend[postgres]"

# -------------------------------------------------------------- runtime stage
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    JARVIS_ENV=prod JARVIS_HOST=0.0.0.0 JARVIS_PORT=8077 \
    JARVIS_LOG_FORMAT=json JARVIS_LOG_TO_FILE=false \
    JARVIS_DATA_DIR=/tmp/jarvis

RUN groupadd --system --gid 10001 jarvis \
 && useradd  --system --uid 10001 --gid jarvis --no-create-home --shell /usr/sbin/nologin jarvis
COPY --from=build /opt/venv /opt/venv

USER 10001:10001
EXPOSE 8077

# Liveness only (no dependencies): orchestrators use /readyz for traffic decisions.
HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8077/healthz',timeout=3).status==200 else 1)"

ENTRYPOINT ["python", "-m", "jarvis_backend"]
CMD ["serve"]
