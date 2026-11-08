from app.core.config import settings
from app.main import app
from fastapi.testclient import TestClient


def test_create_project_and_read_capabilities(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    client = TestClient(app)

    created = client.post(
        "/v1/projects",
        json={
            "name": "مشروع تجريبي",
            "target_duration_seconds": 60,
            "target_resolution": "1920x1080",
            "target_fps": 30,
            "language": "ar",
            "brief": "اختبار استقبال مشروع",
        },
    )
    assert created.status_code == 201
    project_id = created.json()["id"]

    fetched = client.get(f"/v1/projects/{project_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "مشروع تجريبي"

    capabilities = client.get("/v1/system/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["phase"] == "1"
    assert capabilities.json()["external_model_apis_enabled"] is False


def test_unsupported_upload_is_rejected_without_saving(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    client = TestClient(app)
    created = client.post(
        "/v1/projects",
        json={"name": "Test", "target_duration_seconds": 60, "brief": "Test"},
    )
    project_id = created.json()["id"]

    response = client.post(
        f"/v1/projects/{project_id}/assets",
        files={"file": ("not-media.txt", b"not media", "text/plain")},
    )
    assert response.status_code == 422
    assert "Unsupported media extension" in response.json()["detail"]
