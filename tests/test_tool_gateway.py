import io

from app.api.routes import tools as tools_route
from app.core.config import settings
from app.main import app
from app.media.storage import create_project, ingest_file_stream
from app.services import tool_gateway
from fastapi.testclient import TestClient


def _project_with_asset(tmp_path, *, filename="source.mp4"):
    settings.media_root = str(tmp_path / "media")
    project = create_project(
        {
            "name": "Unified tools test",
            "target_duration_seconds": 30,
            "brief": "Test the shared tool gateway.",
        }
    )
    asset, _ = ingest_file_stream(
        project["id"],
        filename,
        io.BytesIO(b"synthetic-media"),
        max_bytes=1024,
    )
    return project, asset


def test_tool_catalog_exposes_a_single_dispatch_contract(monkeypatch):
    monkeypatch.setattr(
        tool_gateway,
        "get_model_capabilities",
        lambda: {
            "models": {
                "qwen3.5-9b-q4_K_M": {"ready": False, "configured": False},
                "whisperx": {"worker_enabled": False},
                "deepfilternet": {"configured": False},
                "blender": {"executable_configured": False},
            },
            "runtimes": {"comfyui": {"reachable": False}},
        },
    )

    catalog = tool_gateway.get_tool_catalog()
    ids = {item["tool_id"] for item in catalog["tools"]}

    assert catalog["dispatch_endpoint"] == "/v1/tools/execute"
    assert "director.plan" in ids
    assert "speech.transcribe" in ids
    assert "vision.detect" in ids
    assert "video.generate" in ids
    assert "motion.blender" in ids


def test_unified_gateway_submits_durable_job_and_replays_idempotently(tmp_path, monkeypatch):
    project, asset = _project_with_asset(tmp_path)
    client = TestClient(app)
    payload = {
        "tool_id": "media.proxy",
        "project_id": project["id"],
        "asset_id": asset["id"],
        "payload": {"max_width": 640, "fps": 24},
        "idempotency_key": "gateway-proxy-0001",
    }

    first = client.post("/v1/tools/execute", json=payload)
    assert first.status_code == 202, first.text
    assert first.json()["kind"] == "job"
    assert first.json()["job"]["job_type"] == "media.proxy"

    replay = client.post("/v1/tools/execute", json=payload)
    assert replay.status_code == 202
    assert replay.headers["Idempotent-Replay"] == "true"
    assert replay.json()["job"]["id"] == first.json()["job"]["id"]


def test_unified_gateway_routes_director_to_approval_gated_plan(monkeypatch):
    class FakeGraph:
        def invoke(self, state):
            return {
                "creative_brief": {"title": "خطة تجريبية", "scenes": []},
                "status": "awaiting_approval",
            }

    monkeypatch.setattr(tools_route, "build_graph", lambda: FakeGraph())
    client = TestClient(app)
    response = client.post(
        "/v1/tools/execute",
        json={
            "tool_id": "director.plan",
            "brief": "Create a short cinematic product video for a new watch.",
            "target_duration_seconds": 30,
            "language": "en",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "plan"
    assert response.json()["approval_required"] is True
    assert response.json()["status"] == "awaiting_approval"


def test_unified_gateway_rejects_unknown_tool_id():
    client = TestClient(app)
    response = client.post(
        "/v1/tools/execute",
        json={
            "tool_id": "shell.execute",
            "project_id": "00000000-0000-4000-8000-000000000001",
            "asset_id": "00000000-0000-4000-8000-000000000002",
            "idempotency_key": "gateway-unknown-0001",
        },
    )
    assert response.status_code == 422


def test_grounding_dino_gateway_accepts_an_image_asset(tmp_path, monkeypatch):
    project, asset = _project_with_asset(tmp_path, filename="reference.png")
    monkeypatch.setattr(settings, "comfyui_base_url", "http://127.0.0.1:8188")
    client = TestClient(app)

    response = client.post(
        "/v1/tools/execute",
        json={
            "tool_id": "vision.detect",
            "project_id": project["id"],
            "asset_id": asset["id"],
            "payload": {"prompt": "person"},
            "idempotency_key": "gateway-detect-0001",
        },
    )

    assert response.status_code == 202, response.text
    assert response.json()["job"]["queue_name"] == "models"
