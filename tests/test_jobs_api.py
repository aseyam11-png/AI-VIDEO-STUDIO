import io
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.db.models import JobRecord, OutboxRecord
from app.db.session import session_scope
from app.main import app
from app.media.storage import create_project, ingest_file_stream
from app.services.job_runner import _finish_job, recover_stale_jobs, run_job_attempt
from app.worker import dispatch_outbox_batch
from fastapi.testclient import TestClient
from sqlalchemy import select


def _project_with_video(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    project = create_project(
        {"name": "Jobs test", "target_duration_seconds": 30, "brief": "Durable jobs test"}
    )
    asset, _ = ingest_file_stream(
        project["id"], "fixture.mp4", io.BytesIO(b"test-bytes"), max_bytes=1024
    )
    return project, asset


def test_job_idempotency_cancel_retry_and_events(tmp_path, monkeypatch):
    project, asset = _project_with_video(tmp_path, monkeypatch)
    client = TestClient(app)
    payload = {
        "project_id": project["id"],
        "asset_id": asset["id"],
        "job_type": "media.proxy",
        "payload": {"max_width": 640, "fps": 24},
        "idempotency_key": "proxy-request-0001",
    }

    created = client.post("/v1/jobs", json=payload)
    assert created.status_code == 202, created.text
    first = created.json()
    assert first["status"] == "queued"
    assert first["progress"] == 0

    replay = client.post("/v1/jobs", json=payload)
    assert replay.status_code == 202
    assert replay.headers["Idempotent-Replay"] == "true"
    assert replay.json()["id"] == first["id"]

    changed = {**payload, "payload": {"max_width": 1280, "fps": 24}}
    conflict = client.post("/v1/jobs", json=changed)
    assert conflict.status_code == 409

    events = client.get(f"/v1/jobs/{first['id']}/events")
    assert events.status_code == 200
    assert [event["event_type"] for event in events.json()] == ["created"]

    cancelled = client.post(f"/v1/jobs/{first['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    retried = client.post(f"/v1/jobs/{first['id']}/retry")
    assert retried.status_code == 202
    assert retried.json()["status"] == "queued"
    assert retried.json()["retry_count"] == 1

    listed = client.get("/v1/jobs", params={"project_id": project["id"]})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


def test_job_payload_is_allowlisted_and_asset_is_project_scoped(tmp_path, monkeypatch):
    project, asset = _project_with_video(tmp_path, monkeypatch)
    client = TestClient(app)
    base = {
        "project_id": project["id"],
        "asset_id": asset["id"],
        "job_type": "media.proxy",
        "idempotency_key": "proxy-request-0002",
    }

    unexpected = client.post(
        "/v1/jobs",
        json={**base, "payload": {"max_width": 640, "fps": 24, "shell_command": "whoami"}},
    )
    assert unexpected.status_code == 422

    other = create_project(
        {"name": "Other", "target_duration_seconds": 10, "brief": "Separate project"}
    )
    mismatch = client.post("/v1/jobs", json={**base, "project_id": other["id"], "payload": {}})
    assert mismatch.status_code == 422


def test_outbox_dispatch_publishes_only_committed_job_messages(tmp_path, monkeypatch):
    project, asset = _project_with_video(tmp_path, monkeypatch)
    client = TestClient(app)
    created = client.post(
        "/v1/jobs",
        json={
            "project_id": project["id"],
            "asset_id": asset["id"],
            "job_type": "media.proxy",
            "payload": {},
            "idempotency_key": "proxy-request-0003",
        },
    )
    assert created.status_code == 202
    job_id = created.json()["id"]
    messages = []

    published = dispatch_outbox_batch(
        lambda name, **kwargs: messages.append((name, kwargs)),
        limit=10,
    )
    assert published == 1
    assert messages[0][0] == "studio.execute_job"
    assert messages[0][1]["args"] == [job_id, 1]
    with session_scope() as db:
        outbox = db.scalar(select(OutboxRecord).where(OutboxRecord.job_id == job_id))
        assert outbox.status == "published"


def test_stale_running_job_is_requeued_with_new_generation(tmp_path, monkeypatch):
    project, asset = _project_with_video(tmp_path, monkeypatch)
    client = TestClient(app)
    created = client.post(
        "/v1/jobs",
        json={
            "project_id": project["id"],
            "asset_id": asset["id"],
            "job_type": "media.proxy",
            "payload": {},
            "idempotency_key": "proxy-request-0004",
        },
    )
    job_id = created.json()["id"]
    with session_scope() as db:
        job = db.get(JobRecord, job_id)
        job.status = "running"
        job.attempt_count = 1
        job.started_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        job.heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=10)

    recovered = recover_stale_jobs()
    assert recovered == 1
    # A late result from the old worker attempt must not overwrite the newer generation.
    _finish_job(job_id, 1, result={"late": True})
    with session_scope() as db:
        job = db.get(JobRecord, job_id)
        assert job.status == "queued"
        assert job.dispatch_generation == 2
        assert job.result_json is None
        assert db.scalar(
            select(OutboxRecord).where(
                OutboxRecord.job_id == job_id,
                OutboxRecord.dispatch_generation == 2,
            )
        ) is not None


def test_worker_rejects_stale_duplicate_delivery(tmp_path, monkeypatch):
    project, asset = _project_with_video(tmp_path, monkeypatch)
    client = TestClient(app)
    created = client.post(
        "/v1/jobs",
        json={
            "project_id": project["id"],
            "asset_id": asset["id"],
            "job_type": "media.proxy",
            "payload": {},
            "idempotency_key": "proxy-request-0005",
        },
    )
    result = run_job_attempt(created.json()["id"], 0)
    assert result == {"status": "ignored", "reason": "stale_or_duplicate_delivery"}



def test_running_cancel_is_recorded_as_cooperative_request(tmp_path, monkeypatch):
    project, asset = _project_with_video(tmp_path, monkeypatch)
    client = TestClient(app)
    created = client.post(
        "/v1/jobs",
        json={
            "project_id": project["id"],
            "asset_id": asset["id"],
            "job_type": "media.proxy",
            "payload": {},
            "idempotency_key": "proxy-request-0006",
        },
    )
    job_id = created.json()["id"]
    with session_scope() as db:
        job = db.get(JobRecord, job_id)
        job.status = "running"
        job.attempt_count = 1

    response = client.post(f"/v1/jobs/{job_id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancel_requested"
    assert response.json()["cancel_requested"] is True
