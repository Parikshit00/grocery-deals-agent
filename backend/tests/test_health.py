"""The app boots and reports its dependencies honestly when nothing is running."""
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def _client(monkeypatch) -> TestClient:
    # Point every dependency at a closed local port so the checks fail fast and deterministically.
    for key, value in {
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": "9",
        "LLM_BASE_URL": "http://127.0.0.1:9/v1",
        "VLM_BASE_URL": "http://127.0.0.1:9/v1",
        "EMBEDDING_MODEL": "/nonexistent/bge-m3",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    return TestClient(create_app())


def test_health_ok(monkeypatch):
    resp = _client(monkeypatch).get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_status_reports_models_down(monkeypatch):
    import app.api.health as health

    monkeypatch.setattr(health, "_cache", None)
    body = _client(monkeypatch).get("/api/status").json()
    assert body["llm"] == "down"
    assert body["vlm"] == "down"
    assert body["embeddings"] == "missing"
    assert body["chains"] == []
