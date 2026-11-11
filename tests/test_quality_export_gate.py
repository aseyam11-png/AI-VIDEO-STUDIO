import shutil
import subprocess
from uuid import uuid4

import pytest
from app.core.config import settings
from app.main import app
from fastapi.testclient import TestClient


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="FFmpeg is required for export quality gate integration tests",
)


def _make_clip(path, *, black=False):
    command = [
        shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i",
        "color=c=black:s=320x240:r=24:d=2" if black else "testsrc2=size=320x240:rate=24:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=550:sample_rate=48000:duration=2",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(path),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)


def _project(client):
    response = client.post(
        "/v1/projects",
        json={
            "name": "Quality gate integration",
            "target_duration_seconds": 2,
            "target_resolution": "640x360",
            "target_fps": 24,
            "language": "ar",
            "brief": "Verify that unsafe renders cannot be downloaded.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload(client, project_id, path):
    with path.open("rb") as stream:
        response = client.post(
            f"/v1/projects/{project_id}/assets",
            files={"file": (path.name, stream, "video/mp4")},
        )
    assert response.status_code == 201, response.text
    return response.json()


def test_multi_source_export_is_blocked_for_black_frames(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    source = tmp_path / "black.mp4"
    _make_clip(source, black=True)
    client = TestClient(app)
    project = _project(client)
    asset = _upload(client, project["id"], source)

    saved = client.put(
        f"/v1/projects/{project['id']}/timeline",
        json={
            "fps": 24,
            "clips": [
                {
                    "clip_id": str(uuid4()),
                    "asset_id": asset["id"],
                    "source_start_seconds": 0,
                    "duration_seconds": 2,
                }
            ],
        },
    )
    assert saved.status_code == 200, saved.text

    rendered = client.post(
        f"/v1/projects/{project['id']}/timeline/render",
        json={"width": 640, "height": 360, "fps": 24, "crf": 24},
    )
    assert rendered.status_code == 422, rendered.text
    report = rendered.json()["detail"]["quality_report"]
    assert report["can_export"] is False
    assert any(issue["code"] == "black_frames_detected" for issue in report["issues"])

    download = client.get(f"/v1/projects/{project['id']}/timeline/rendered")
    assert download.status_code == 404


def test_multi_source_export_returns_report_for_clean_video(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    source = tmp_path / "moving.mp4"
    _make_clip(source, black=False)
    client = TestClient(app)
    project = _project(client)
    asset = _upload(client, project["id"], source)

    saved = client.put(
        f"/v1/projects/{project['id']}/timeline",
        json={
            "fps": 24,
            "clips": [
                {
                    "clip_id": str(uuid4()),
                    "asset_id": asset["id"],
                    "source_start_seconds": 0,
                    "duration_seconds": 2,
                }
            ],
        },
    )
    assert saved.status_code == 200, saved.text

    rendered = client.post(
        f"/v1/projects/{project['id']}/timeline/render",
        json={"width": 640, "height": 360, "fps": 24, "crf": 24},
    )
    assert rendered.status_code == 200, rendered.text
    report = rendered.json()["quality_report"]
    assert report["can_export"] is True
    assert report["file"]["sha256"]

    download = client.get(f"/v1/projects/{project['id']}/timeline/rendered")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("video/mp4")
