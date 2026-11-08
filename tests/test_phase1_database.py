import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.models import AssetRecord, JobRecord, ProjectRecord
from app.db.session import configure_database, get_engine, session_scope
from app.main import app
from app.services.legacy_import import import_legacy_manifests


def test_alembic_upgrade_creates_phase1_schema(tmp_path, monkeypatch):
    database_path = tmp_path / "migration-test.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(settings, "database_url", database_url)
    configure_database(database_url)

    config = Config("alembic.ini")
    command.upgrade(config, "head")

    inspector = inspect(get_engine())
    tables = set(inspector.get_table_names())
    assert {
        "alembic_version",
        "projects",
        "assets",
        "jobs",
        "job_events",
        "outbox_events",
    }.issubset(tables)
    assert "uq_jobs_project_idempotency" in {
        item["name"] for item in inspector.get_unique_constraints("jobs")
    }
    ready = TestClient(app).get("/ready")
    assert ready.status_code == 200, ready.text


def test_legacy_import_is_idempotent_and_keeps_media_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "legacy-media"))
    project_id = str(uuid4())
    asset_id = str(uuid4())
    project_dir = tmp_path / "legacy-media" / "projects" / project_id
    asset_dir = project_dir / "assets" / asset_id
    asset_dir.mkdir(parents=True)
    source = asset_dir / "source.mp4"
    source.write_bytes(b"do-not-move-or-rewrite")

    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "id": project_id,
                "status": "created",
                "created_at": "2026-11-08T15:00:00+00:00",
                "name": "Legacy project",
                "target_duration_seconds": 30,
                "target_resolution": "640x360",
                "target_fps": 24,
                "language": "ar",
                "brief": "Imported without moving media",
            }
        ),
        encoding="utf-8",
    )
    (asset_dir / "asset.json").write_text(
        json.dumps(
            {
                "id": asset_id,
                "project_id": project_id,
                "asset_type": "video",
                "original_filename": "source.mp4",
                "stored_filename": "source.mp4",
                "relative_path": f"assets/{asset_id}/source.mp4",
                "size_bytes": source.stat().st_size,
                "sha256": "a" * 64,
                "created_at": "2026-11-08T15:01:00+00:00",
                "status": "ready",
                "probe": {"format": {"duration": "1.0"}},
            }
        ),
        encoding="utf-8",
    )

    first = import_legacy_manifests()
    second = import_legacy_manifests()

    assert first == {"projects_imported": 1, "assets_imported": 1}
    assert second == {"projects_imported": 0, "assets_imported": 0}
    assert source.read_bytes() == b"do-not-move-or-rewrite"



def test_database_rejects_job_asset_from_another_project():
    now = datetime.now(timezone.utc)
    project_one = str(uuid4())
    project_two = str(uuid4())
    asset_id = str(uuid4())

    with pytest.raises(IntegrityError):
        with session_scope() as db:
            db.add_all(
                [
                    ProjectRecord(
                        id=project_one,
                        name="Project one",
                        target_duration_seconds=10,
                        target_resolution="640x360",
                        target_fps=24,
                        language="ar",
                        brief="First project",
                        status="created",
                    ),
                    ProjectRecord(
                        id=project_two,
                        name="Project two",
                        target_duration_seconds=10,
                        target_resolution="640x360",
                        target_fps=24,
                        language="ar",
                        brief="Second project",
                        status="created",
                    ),
                ]
            )
            db.flush()
            db.add(
                AssetRecord(
                    id=asset_id,
                    project_id=project_one,
                    asset_type="video",
                    original_filename="source.mp4",
                    stored_filename="source.mp4",
                    relative_path=f"assets/{asset_id}/source.mp4",
                    size_bytes=10,
                    sha256="a" * 64,
                    status="ready",
                    metadata_json={},
                    created_at=now,
                    updated_at=now,
                )
            )
            db.flush()
            db.add(
                JobRecord(
                    id=str(uuid4()),
                    project_id=project_two,
                    asset_id=asset_id,
                    job_type="media.proxy",
                    queue_name="cpu",
                    status="queued",
                    progress=0,
                    progress_stage="queued",
                    progress_message="Queued",
                    payload_json={},
                    result_json=None,
                    idempotency_key="cross-project-key-01",
                    request_hash="b" * 64,
                    attempt_count=0,
                    max_attempts=3,
                    retry_count=0,
                    dispatch_generation=1,
                    cancel_requested=False,
                    created_at=now,
                    updated_at=now,
                )
            )



def test_readiness_rejects_database_without_alembic_revision():
    response = TestClient(app).get("/ready")
    assert response.status_code == 503
