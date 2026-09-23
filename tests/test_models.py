"""Offline checks for provider-specific chat configuration and routing."""

from fastapi.testclient import TestClient

from neova.app import app
from neova import models


def test_chat_supports_both_providers(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'neova.db'}")
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-secret")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("CHAT_MODEL", "router-model")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("OPENAI_CHAT_MODEL", "openai-model")
    calls = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def invoke(self, prompt):
            calls.append(prompt)
            return type("Reply", (), {"content": "Bonjour"})()

    monkeypatch.setattr(models, "ChatOpenAI", FakeChatOpenAI)
    with TestClient(app) as client:
        for provider in ("openrouter", "openai"):
            response = client.post("/models/chat", json={"provider": provider, "prompt": "Salut"})
            assert response.status_code == 200
            assert response.json() == {"provider": provider, "output": "Bonjour"}
        assert client.post("/models/chat", json={"provider": "other", "prompt": "Salut"}).status_code == 422

    assert calls == [
        {"model": "router-model", "api_key": "router-secret", "base_url": "https://openrouter.ai/api/v1"},
        "Salut",
        {"model": "openai-model", "api_key": "openai-secret", "base_url": "https://api.openai.com/v1"},
        "Salut",
    ]


def test_chat_requires_only_selected_provider_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'neova.db'}")
    monkeypatch.setenv("OPENAI_CHAT_MODEL", "openai-model")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(app) as client:
        response = client.post("/models/chat", json={"provider": "openai", "prompt": "Salut"})
    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]
