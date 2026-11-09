from app.core.config import settings
from app.main import app
from fastapi.testclient import TestClient


def test_studio_ui_and_static_assets_are_served():
    client = TestClient(app)

    home = client.get("/", follow_redirects=False)
    assert home.status_code == 307
    assert home.headers["location"] == "/studio/"

    page = client.get("/studio/")
    assert page.status_code == 200
    assert 'lang="ar" dir="rtl"' in page.text
    assert "مراجعة الخطة الإبداعية" in page.text

    styles = client.get("/studio/styles.css")
    assert styles.status_code == 200
    assert "--accent" in styles.text

    script = client.get("/studio/app.js")
    assert script.status_code == 200
    assert "/v1/workflows/from-brief" in script.text
    assert "/v1/models/status" in script.text
    assert "لم يُعتمد الاستدلال الحقيقي بعد" in script.text


def test_project_history_endpoint_lists_created_projects(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    client = TestClient(app)

    created = client.post(
        "/v1/projects",
        json={
            "name": "Arabic Studio UI test",
            "target_duration_seconds": 30,
            "target_resolution": "1280x720",
            "target_fps": 30,
            "language": "ar",
            "brief": "اختبار قائمة المشاريع في واجهة الاستديو.",
        },
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    response = client.get("/v1/projects?limit=10")
    assert response.status_code == 200, response.text
    items = response.json()
    assert len(items) == 1
    assert items[0]["id"] == project_id
    assert items[0]["name"] == "Arabic Studio UI test"
