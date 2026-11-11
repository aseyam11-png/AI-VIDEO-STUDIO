import hashlib
import io

from fastapi.testclient import TestClient

from app.core.config import settings
from app.media.storage import atomic_write_json, create_project, ingest_file_stream, project_dir
from app.main import app


def _project_and_video_asset(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    project = create_project(
        {
            "name": "Quality gate download test",
            "target_duration_seconds": 10,
            "target_resolution": "640x360",
            "target_fps": 24,
            "language": "ar",
            "brief": "Verify the export gate.",
        }
    )
    asset, _ = ingest_file_stream(
        project["id"],
        "source.mp4",
        io.BytesIO(b"synthetic-source-file"),
        max_bytes=1024,
    )
    return project, asset


def _write_report(rendered, content, *, passed):
    report = {
        "schema_version": 1,
        "file": {
            "name": rendered.name,
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
        "summary": {"failed_checks": [] if passed else ["decode"]},
        "passed": passed,
    }
    atomic_write_json(rendered.with_suffix(".quality.json"), report)


def test_asset_export_fails_closed_until_a_matching_passing_report_exists(
    tmp_path, monkeypatch
):
    project, asset = _project_and_video_asset(tmp_path, monkeypatch)
    rendered = project_dir(project["id"]) / "exports" / f"{asset['id']}-preview.mp4"
    rendered.parent.mkdir(parents=True, exist_ok=True)
    content = b"test-export-bytes"
    rendered.write_bytes(content)
    client = TestClient(app)
    url = f"/v1/projects/{project['id']}/assets/{asset['id']}/rendered"

    missing = client.get(url)
    assert missing.status_code == 409

    _write_report(rendered, content, passed=False)
    failed = client.get(url)
    assert failed.status_code == 409
    assert "quality gate failed" in failed.json()["detail"]["message"].lower()

    _write_report(rendered, content, passed=True)
    accepted = client.get(url)
    assert accepted.status_code == 200
    assert accepted.content == content

    rendered.write_bytes(b"changed-after-quality-analysis")
    changed = client.get(url)
    assert changed.status_code == 409
    assert "changed after" in changed.json()["detail"].lower()


def test_project_timeline_export_also_requires_a_passing_report(tmp_path, monkeypatch):
    project, _ = _project_and_video_asset(tmp_path, monkeypatch)
    rendered = (
        project_dir(project["id"])
        / "exports"
        / f"project-{project['id']}-preview.mp4"
    )
    rendered.parent.mkdir(parents=True, exist_ok=True)
    content = b"project-preview-bytes"
    rendered.write_bytes(content)
    client = TestClient(app)
    url = f"/v1/projects/{project['id']}/timeline/rendered"

    assert client.get(url).status_code == 409
    _write_report(rendered, content, passed=True)
    response = client.get(url)
    assert response.status_code == 200
    assert response.content == content
