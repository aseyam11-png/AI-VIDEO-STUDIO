from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


def test_local_development_allows_access_without_token(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "studio_api_token", None)

    client = TestClient(app)
    response = client.get("/v1/auth/session")

    assert response.status_code == 200
    assert response.json()["auth_required"] is False
    assert response.headers["x-content-type-options"] == "nosniff"


def test_configured_token_protects_api_and_is_checked_by_session_endpoint(monkeypatch):
    token = "studio-test-token-" + ("x" * 40)
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "studio_api_token", token)
    client = TestClient(app)

    assert client.get("/v1/system/capabilities").status_code == 401
    assert client.get("/v1/auth/session").status_code == 401

    authorized_session = client.get(
        "/v1/auth/session",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert authorized_session.status_code == 200
    assert authorized_session.json()["auth_required"] is True

    authorized_api = client.get(
        "/v1/system/capabilities",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert authorized_api.status_code == 200


def test_production_fails_closed_when_token_is_missing_or_too_short(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "studio_api_token", None)
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/v1/auth/session").status_code == 503
    assert client.get("/v1/system/capabilities").status_code == 503

    monkeypatch.setattr(settings, "studio_api_token", "short")
    assert client.get("/v1/system/capabilities").status_code == 503


def test_production_requires_valid_bearer_token(monkeypatch):
    token = "production-studio-token-" + ("s" * 40)
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "studio_api_token", token)
    client = TestClient(app)

    assert client.get("/v1/system/capabilities").status_code == 401
    response = client.get(
        "/v1/system/capabilities",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.headers["x-frame-options"] == "DENY"
