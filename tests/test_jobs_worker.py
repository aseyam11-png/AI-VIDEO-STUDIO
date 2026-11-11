import shutil
import subprocess

from app.core.config import settings
from app.main import app
from app.services.job_runner import run_job_attempt
from fastapi.testclient import TestClient
import pytest


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="FFmpeg required for background media job test",
)
def test_background_worker_executes_full_single_asset_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    source = tmp_path / "background-fixture.mp4"
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
        "sine=frequency=660:sample_rate=48000:duration=2",
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
    project = client.post(
        "/v1/projects",
        json={
            "name": "Background pipeline",
            "target_duration_seconds": 2,
            "target_resolution": "640x360",
            "target_fps": 24,
            "language": "ar",
            "brief": "Exercise the durable Celery job handler without connecting to a broker.",
        },
    ).json()
    with source.open("rb") as stream:
        uploaded = client.post(
            f"/v1/projects/{project['id']}/assets",
            files={"file": ("background-fixture.mp4", stream, "video/mp4")},
        )
    assert uploaded.status_code == 201, uploaded.text
    asset_id = uploaded.json()["id"]

    cases = [
        ("media.proxy", {"max_width": 640, "fps": 24}),
        ("media.scenes", {"threshold": 27.0, "min_scene_len": 10}),
        ("media.timeline", {"fps": 24}),
        ("media.render", {"width": 640, "height": 360, "fps": 24, "crf": 24}),
    ]
    for index, (job_type, options) in enumerate(cases, start=1):
        created = client.post(
            "/v1/jobs",
            json={
                "project_id": project["id"],
                "asset_id": asset_id,
                "job_type": job_type,
                "payload": options,
                "idempotency_key": f"background-job-{index:04d}",
            },
        )
        assert created.status_code == 202, created.text
        result = run_job_attempt(created.json()["id"], 1)
        assert result["status"] == "succeeded"

    export = client.get(
        f"/v1/projects/{project['id']}/assets/{asset_id}/rendered"
    )
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("video/mp4")
    quality = client.get(
        f"/v1/projects/{project['id']}/quality-report?asset_id={asset_id}"
    )
    assert quality.status_code == 200, quality.text
    assert quality.json()["can_export"] is True
    assert quality.json()["file"]["sha256"]
    assert quality.json()["metrics"]["width"] == 640
