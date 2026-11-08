import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.config import settings
from app.main import app
from fastapi.testclient import TestClient


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="FFmpeg is required for media integration test",
)
def test_video_ingest_proxy_scenes_timeline_and_render(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    source = tmp_path / "source.mp4"
    command = [
        shutil.which("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x240:rate=24:duration=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=880:sample_rate=48000:duration=2",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(source),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)

    client = TestClient(app)
    project_response = client.post(
        "/v1/projects",
        json={
            "name": "Phase 0.5 integration",
            "target_duration_seconds": 2,
            "target_resolution": "640x360",
            "target_fps": 24,
            "language": "ar",
            "brief": "Deterministic media pipeline integration fixture",
        },
    )
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    with source.open("rb") as stream:
        upload_response = client.post(
            f"/v1/projects/{project_id}/assets",
            files={"file": ("fixture.mp4", stream, "video/mp4")},
        )
    assert upload_response.status_code == 201, upload_response.text
    asset_id = upload_response.json()["id"]

    proxy_response = client.post(
        f"/v1/projects/{project_id}/assets/{asset_id}/proxy",
        json={"max_width": 640, "fps": 24},
    )
    assert proxy_response.status_code == 200, proxy_response.text

    scenes_response = client.post(
        f"/v1/projects/{project_id}/assets/{asset_id}/scenes",
        json={"threshold": 27.0, "min_scene_len": 10},
    )
    assert scenes_response.status_code == 200, scenes_response.text
    assert scenes_response.json()["scene_count"] >= 1

    timeline_response = client.post(
        f"/v1/projects/{project_id}/assets/{asset_id}/timeline",
        json={"fps": 24},
    )
    assert timeline_response.status_code == 200, timeline_response.text

    render_response = client.post(
        f"/v1/projects/{project_id}/assets/{asset_id}/render",
        json={"width": 640, "height": 360, "fps": 24, "crf": 24},
    )
    assert render_response.status_code == 200, render_response.text
    assert render_response.json()["size_bytes"] > 0

    download_response = client.get(
        f"/v1/projects/{project_id}/assets/{asset_id}/rendered"
    )
    assert download_response.status_code == 200
    assert download_response.headers["content-type"].startswith("video/mp4")
