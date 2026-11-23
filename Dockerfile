# Pin the multi-platform image digest; update it deliberately for security fixes.
FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies before copying source so code changes reuse this layer.
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app

COPY hello_world.py ./

# The source is authoritative. Fail the build if the supplied tag disagrees.
ARG VERSION
RUN python -c 'import re, sys; from hello_world import APP_VERSION; assert re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", APP_VERSION), "APP_VERSION must be Major.Minor.Patch"; assert sys.argv[1] == APP_VERSION, "VERSION must match APP_VERSION in hello_world.py"' "$VERSION"

LABEL org.opencontainers.image.title="mentee-test" \
    org.opencontainers.image.version="${VERSION}"

# Application files remain root-owned; the runtime user can only read them.
USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).close()"]

# Exec form lets Gunicorn receive termination signals for graceful shutdown.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--worker-tmp-dir", "/dev/shm", "--access-logfile", "-", "--error-logfile", "-", "hello_world:app"]
