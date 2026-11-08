import hashlib
import io

import pytest
from app.core.config import settings
from app.media.storage import (
    asset_dir,
    create_project,
    get_asset,
    get_project,
    ingest_file_stream,
)


def test_project_manifest_is_created_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    project = create_project({
        "name": "Arabic test project",
        "target_duration_seconds": 60,
        "brief": "Test only",
    })

    assert get_project(project["id"])["name"] == "Arabic test project"
    assert (tmp_path / "projects" / project["id"] / "project.json").is_file()


def test_ingest_stores_safe_uuid_name_and_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    project = create_project({"name": "Test", "target_duration_seconds": 60, "brief": "Test"})
    payload = b"not a real video; storage layer does not decode content"
    manifest, stored = ingest_file_stream(
        project["id"], "../../private-name.mp4", io.BytesIO(payload), max_bytes=1024
    )

    assert stored.name == "source.mp4"
    assert stored.is_relative_to(asset_dir(project["id"], manifest["id"]))
    assert manifest["sha256"] == hashlib.sha256(payload).hexdigest()
    assert get_asset(project["id"], manifest["id"])["original_filename"] == "private-name.mp4"


def test_upload_rejects_unsupported_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    project = create_project({"name": "Test", "target_duration_seconds": 60, "brief": "Test"})

    with pytest.raises(ValueError, match="Unsupported media extension"):
        ingest_file_stream(
            project["id"], "secrets.txt", io.BytesIO(b"private"), max_bytes=1024
        )


def test_upload_enforces_size_limit_and_removes_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    project = create_project({"name": "Test", "target_duration_seconds": 60, "brief": "Test"})

    with pytest.raises(ValueError, match="maximum size"):
        ingest_file_stream(
            project["id"], "oversized.mp4", io.BytesIO(b"x" * 100), max_bytes=10
        )

    assets = tmp_path / "projects" / project["id"] / "assets"
    assert not assets.exists() or list(assets.iterdir()) == []
