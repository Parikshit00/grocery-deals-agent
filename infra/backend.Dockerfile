# API + scan agents. Playwright base image ships Chromium and its system deps.
# Build from the repo root: docker compose -f infra/docker-compose.yml build backend
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

WORKDIR /srv
COPY pyproject.toml README.md LICENSE ./
COPY backend/ backend/
RUN pip install --no-cache-dir .[agents,scraping]

EXPOSE 28734
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=20 \
  CMD python -c "import urllib.request as u; u.urlopen('http://localhost:28734/readyz', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "28734"]
