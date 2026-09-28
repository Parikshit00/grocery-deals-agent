# API + chain agents. The Playwright base ships Chromium and its system deps; its tag must equal
# the playwright pin in pyproject.toml. Build from the repo root (see Makefile).
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble@sha256:8ff591d613b01c884cc488339ed4318b4513eaf0c57a164a878ba49e70e3f384 AS runtime

COPY requirements.lock /srv/requirements.lock
RUN pip install --no-cache-dir -r /srv/requirements.lock

WORKDIR /srv/backend
COPY backend/ /srv/backend/
ENV PYTHONPATH=/srv/backend PYTHONUNBUFFERED=1
USER pwuser

EXPOSE 28734
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=20 \
  CMD python -c "import urllib.request as u; u.urlopen('http://localhost:28734/readyz', timeout=4)"
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 28734"]

# Test/lint toolchain on top of the exact runtime dependencies (used by `make check` and CI).
FROM runtime AS dev
USER root
COPY requirements-dev.lock /srv/requirements-dev.lock
RUN pip install --no-cache-dir -r /srv/requirements-dev.lock
