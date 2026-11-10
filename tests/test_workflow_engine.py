import shutil
import subprocess
from pathlib import Path

import pytest
from app.core.config import settings
from app.db.models import OutboxRecord
from app.db.session import session_scope
from app.main import app
from app.media.storage import (
    create_project,
    ingest_file_stream,
    project_dir,
    register_generated_video_asset,
)
from app.schemas.models import CreativePlan
from app.services import job_runner, workflow_engine
from app.services.job_runner import run_job_attempt
from fastapi.testclient import TestClient


def _plan(duration: int = 15) -> CreativePlan:
    return CreativePlan.model_validate(
        {
            "title": "إعلان سينمائي تجريبي",
            "logline": "فيديو قصير يعرض المنتج بأسلوب سينمائي أنيق.",
            "target_duration_seconds": duration,
            "audience": "الجمهور المهتم بالمنتجات الراقية",
            "visual_style": "سينمائي واقعي وإضاءة ناعمة",
            "color_palette": ["أسود", "ذهبي"],
            "audio_direction": "موسيقى هادئة مع مؤثرات دقيقة",
            "scenes": [
                {
                    "index": 1,
                    "duration_seconds": duration,
                    "purpose": "تقديم المنتج",
                    "visual_description": "لقطة قريبة واضحة للمنتج في خلفية أنيقة",
                    "shot_type": "لقطة قريبة",
                    "camera_movement": "حركة بطيئة للأمام",
                    "narration": "",
                    "on_screen_text": "التفاصيل تصنع الفرق",
                    "sound_design": "موسيقى هادئة",
                }
            ],
            "production_notes": [],
        }
    )


def _project_asset(tmp_path, monkeypatch, *, filename: str, content: bytes):
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    project = create_project(
        {
            "name": "Workflow acceptance",
            "target_duration_seconds": 15,
            "target_resolution": "640x360",
            "target_fps": 24,
            "language": "ar",
            "brief": "اختبار مسار مونتاج عربي متكامل.",
        }
    )
    asset, path = ingest_file_stream(
        project["id"],
        filename,
        __import__("io").BytesIO(content),
        max_bytes=1024 * 1024,
    )
    return project, asset, path


def _make_video(path: Path, duration: int = 2) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is required for workflow render acceptance.")
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=24:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(path),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)


def test_from_arabic_brief_creates_reviewable_workflow_not_an_automatic_export(
    tmp_path, monkeypatch
):
    project, asset, _ = _project_asset(
        tmp_path, monkeypatch, filename="source.mp4", content=b"synthetic-video"
    )
    monkeypatch.setattr(
        workflow_engine,
        "generate_creative_plan",
        lambda brief, *, target_duration_seconds, language: _plan(target_duration_seconds),
    )
    client = TestClient(app)
    response = client.post(
        "/v1/workflows/from-brief",
        json={
            "project_id": project["id"],
            "asset_id": asset["id"],
            "idempotency_key": "workflow-ar-from-brief-0001",
            "brief": "أنشئ مونتاجًا سينمائيًا قصيرًا لهذا الفيديو مع لقطات واضحة وانتقال سلس.",
            "language": "ar",
            "target_duration_seconds": 15,
        },
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "awaiting_approval"
    assert run["approval_required"] is True
    assert run["plan_model"] == settings.ollama_model
    assert [step["tool_id"] for step in run["steps"]] == [
        "media.proxy",
        "media.scenes",
        "media.timeline",
        "media.render",
    ]
    with session_scope() as db:
        outbox_count = db.query(OutboxRecord).count()
    assert outbox_count == 0, "No worker job may run before the user approves the plan."


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is required")
def test_approved_workflow_runs_durable_steps_and_exports_mp4(tmp_path, monkeypatch):
    fixture = tmp_path / "workflow-fixture.mp4"
    _make_video(fixture, duration=2)
    project, asset, _ = _project_asset(
        tmp_path, monkeypatch, filename="source.mp4", content=fixture.read_bytes()
    )
    client = TestClient(app)
    create = client.post(
        "/v1/workflows",
        json={
            "project_id": project["id"],
            "asset_id": asset["id"],
            "idempotency_key": "workflow-render-0001",
            "brief": "اختبار إخراج فيديو من خطة عربية محفوظة.",
            "language": "ar",
            "creative_plan": _plan().model_dump(),
        },
    )
    assert create.status_code == 201, create.text
    workflow_id = create.json()["id"]

    approved = client.post(f"/v1/workflows/{workflow_id}/approve")
    assert approved.status_code == 202, approved.text
    assert approved.json()["status"] == "queued"

    for _ in range(6):
        state = client.get(f"/v1/workflows/{workflow_id}").json()
        if state["status"] in {"succeeded", "failed", "cancelled"}:
            break
        active = next(step for step in state["steps"] if step["status"] == "queued")
        job_response = client.get(f"/v1/jobs/{active['job_id']}")
        assert job_response.status_code == 200, job_response.text
        job = job_response.json()
        result = run_job_attempt(job["id"], job["dispatch_generation"])
        assert result["status"] == "succeeded", result

    final = client.get(f"/v1/workflows/{workflow_id}").json()
    assert final["status"] == "succeeded", final
    assert all(step["status"] == "succeeded" for step in final["steps"])
    last_job_id = final["steps"][-1]["job_id"]
    last_job = client.get(f"/v1/jobs/{last_job_id}").json()
    download_url = last_job["result"]["download_url"]
    export = client.get(download_url)
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("video/mp4")
    assert len(export.content) > 1024


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is required")
def test_generated_video_becomes_next_step_input_asset(tmp_path, monkeypatch):
    fixture = tmp_path / "generated-fixture.mp4"
    _make_video(fixture, duration=2)
    project, image_asset, _ = _project_asset(
        tmp_path, monkeypatch, filename="reference.png", content=b"fake-image-content"
    )
    monkeypatch.setattr(settings, "comfyui_base_url", "http://comfyui.test:8188")

    def fake_comfyui(job, source, *, progress, cancel_check):
        output_dir = project_dir(job["project_id"]) / "ai_outputs" / job["id"]
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / "generated.mp4"
        shutil.copyfile(fixture, output)
        return {
            "outputs": [
                {
                    "saved_as": output.name,
                    "metadata": {"streams": [{"codec_type": "video"}]},
                }
            ],
            "files_directory": output_dir.relative_to(project_dir(job["project_id"])).as_posix(),
        }

    monkeypatch.setattr(job_runner, "execute_comfyui_job", fake_comfyui)
    client = TestClient(app)
    created = client.post(
        "/v1/workflows",
        json={
            "project_id": project["id"],
            "asset_id": image_asset["id"],
            "idempotency_key": "workflow-image-to-video-0001",
            "brief": "أنشئ فيديو من صورة المنتج ثم حسّن الحركة قبل التصدير.",
            "language": "ar",
            "creative_plan": _plan(duration=5).model_dump(),
            "generate_video": True,
            "interpolate_generated_video": True,
        },
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    approved = client.post(f"/v1/workflows/{workflow_id}/approve")
    assert approved.status_code == 202, approved.text

    first = client.get(f"/v1/workflows/{workflow_id}").json()["steps"][0]
    first_job = client.get(f"/v1/jobs/{first['job_id']}").json()
    first_result = run_job_attempt(first_job["id"], first_job["dispatch_generation"])
    assert first_result["status"] == "succeeded"

    after_generation = client.get(f"/v1/workflows/{workflow_id}").json()
    generation = after_generation["steps"][0]
    interpolation = after_generation["steps"][1]
    assert generation["status"] == "succeeded"
    assert generation["result"]["output_asset_id"]
    assert interpolation["asset_id"] == generation["result"]["output_asset_id"]
    generated_asset = client.get(
        f"/v1/projects/{project['id']}/assets/{interpolation['asset_id']}"
    )
    assert generated_asset.status_code == 200
    assert generated_asset.json()["asset_type"] == "video"

    second_job_id = interpolation["job_id"]
    second_job = client.get(f"/v1/jobs/{second_job_id}").json()
    second_result = run_job_attempt(
        second_job["id"], second_job["dispatch_generation"]
    )
    assert second_result["status"] == "succeeded"

    after_interpolation = client.get(
        f"/v1/workflows/{workflow_id}"
    ).json()
    interpolation = after_interpolation["steps"][1]
    timeline = after_interpolation["steps"][2]
    assert interpolation["result"]["output_asset_id"]
    assert timeline["asset_id"] == interpolation["result"]["output_asset_id"]


def test_workflow_approval_is_idempotent_and_cancel_stops_pending_steps(
    tmp_path, monkeypatch
):
    project, asset, _ = _project_asset(
        tmp_path, monkeypatch, filename="source.mp4", content=b"synthetic-video"
    )
    client = TestClient(app)
    created = client.post(
        "/v1/workflows",
        json={
            "project_id": project["id"],
            "asset_id": asset["id"],
            "idempotency_key": "workflow-cancel-0001",
            "brief": "اختبار إيقاف Workflow قبل بدء التنفيذ.",
            "language": "ar",
            "creative_plan": _plan().model_dump(),
        },
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    cancelled = client.post(f"/v1/workflows/{workflow_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert all(step["status"] == "planned" for step in cancelled.json()["steps"])

    create_again = client.post(
        "/v1/workflows",
        json={
            "project_id": project["id"],
            "asset_id": asset["id"],
            "idempotency_key": "workflow-approval-0002",
            "brief": "اختبار الموافقة المتكررة.",
            "language": "ar",
            "creative_plan": _plan().model_dump(),
        },
    )
    assert create_again.status_code == 201
    run_id = create_again.json()["id"]
    first = client.post(f"/v1/workflows/{run_id}/approve")
    second = client.post(f"/v1/workflows/{run_id}/approve")
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["steps"][0]["job_id"] == second.json()["steps"][0]["job_id"]



def test_workflow_request_replays_same_idempotency_key_and_rejects_changed_body(
    tmp_path, monkeypatch
):
    project, asset, _ = _project_asset(
        tmp_path, monkeypatch, filename="source.mp4", content=b"synthetic-video"
    )
    client = TestClient(app)
    payload = {
        "project_id": project["id"],
        "asset_id": asset["id"],
        "idempotency_key": "workflow-idempotency-0001",
        "brief": "اختبار منع إنشاء مسارات إنتاج مكررة.",
        "language": "ar",
        "creative_plan": _plan().model_dump(),
    }
    first = client.post("/v1/workflows", json=payload)
    replay = client.post("/v1/workflows", json=payload)
    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == first.json()["id"]

    changed = {**payload, "brief": "وصف مختلف مع نفس مفتاح منع التكرار."}
    conflict = client.post("/v1/workflows", json=changed)
    assert conflict.status_code == 409, conflict.text


def test_workflow_can_plan_grounding_dino_and_sam2_before_video_editing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "comfyui_base_url", "http://comfyui.test:8188")
    project, asset, _ = _project_asset(
        tmp_path, monkeypatch, filename="source.mp4", content=b"synthetic-video"
    )
    client = TestClient(app)
    payload = {
        "project_id": project["id"],
        "asset_id": asset["id"],
        "idempotency_key": "workflow-vision-tools-0001",
        "brief": "تتبع العنصر الرئيسي ثم جهز المشاهد للتصدير.",
        "language": "ar",
        "creative_plan": _plan().model_dump(),
        "detection_prompt": "the main product",
        "tracking_prompt": "the main product",
    }
    response = client.post("/v1/workflows", json=payload)
    assert response.status_code == 201, response.text
    assert [step["tool_id"] for step in response.json()["steps"][:4]] == [
        "vision.detect",
        "vision.track",
        "media.proxy",
        "media.scenes",
    ]


def _create_identity_locked_workflow_at_review(tmp_path, monkeypatch, *, idempotency_key: str):
    monkeypatch.setattr(settings, "comfyui_base_url", "http://comfyui.test:8188")
    fixture = tmp_path / "identity-review-output.mp4"
    _make_video(fixture, duration=4)
    project, reference_asset, _ = _project_asset(
        tmp_path,
        monkeypatch,
        filename="product-reference.png",
        content=b"synthetic-product-image-reference",
    )

    def fake_sequence(job, source, *, progress, cancel_check):
        assert source.is_file()
        output_dir = project_dir(job["project_id"]) / "exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"{job['id']}-identity-review.mp4"
        shutil.copyfile(fixture, output)
        asset = register_generated_video_asset(
            job["project_id"],
            job["id"],
            output,
            max_bytes=settings.max_model_output_bytes,
        )
        return {
            "output_asset_id": asset["id"],
            "output_asset": asset,
            "download_url": f"/v1/projects/{job['project_id']}/assets/{asset['id']}/file",
        }

    monkeypatch.setattr(job_runner, "execute_video_sequence", fake_sequence)
    client = TestClient(app)
    created = client.post(
        "/v1/workflows",
        json={
            "project_id": project["id"],
            "asset_id": reference_asset["id"],
            "idempotency_key": idempotency_key,
            "brief": "أنشئ إعلانًا قصيرًا من صورة المنتج وحافظ على هويته في كل اللقطات.",
            "language": "ar",
            "creative_plan": _plan().model_dump(),
            "generate_video": True,
            "product_identity_lock": True,
        },
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    approved = client.post(f"/v1/workflows/{workflow_id}/approve")
    assert approved.status_code == 202, approved.text
    generation_step = approved.json()["steps"][0]
    job = client.get(f"/v1/jobs/{generation_step['job_id']}").json()
    assert job["payload"]["product_identity_lock"] is True
    assert job["payload"]["identity_reference_sha256"] == reference_asset["sha256"]
    assert all(
        "Canonical product identity lock" in shot["prompt"]
        for shot in job["payload"]["shots"]
    )
    execution = run_job_attempt(job["id"], job["dispatch_generation"])
    assert execution["status"] == "succeeded"

    pending = client.get(f"/v1/workflows/{workflow_id}").json()
    assert pending["status"] == "awaiting_identity_review"
    assert pending["options"]["product_identity_review"]["status"] == "pending"
    sequence_step, timeline_step, render_step = pending["steps"]
    assert sequence_step["status"] == "succeeded"
    assert timeline_step["status"] == "planned"
    assert timeline_step["job_id"] is None
    assert render_step["status"] == "planned"
    assert render_step["job_id"] is None
    review_video = client.get(pending["options"]["product_identity_review"]["preview_url"])
    assert review_video.status_code == 200
    assert review_video.headers["content-type"].startswith("video/mp4")
    return client, workflow_id, pending


def test_product_identity_lock_pauses_before_timeline_until_checklist_is_approved(
    tmp_path, monkeypatch
):
    client, workflow_id, pending = _create_identity_locked_workflow_at_review(
        tmp_path, monkeypatch, idempotency_key="identity-lock-review-approve-0001"
    )
    checks = {
        "silhouette_and_proportions": True,
        "colors_and_materials": True,
        "logo_and_labels": True,
        "components_and_packaging": True,
        "consistency_across_shots": True,
    }
    incomplete = client.post(
        f"/v1/workflows/{workflow_id}/identity-review",
        json={"approved": True, "notes": "يجب أن ترفض الموافقة الناقصة."},
    )
    assert incomplete.status_code == 422, incomplete.text

    approved = client.post(
        f"/v1/workflows/{workflow_id}/identity-review",
        json={"approved": True, "notes": "تمت مراجعة هوية المنتج.", "checks": checks},
    )
    assert approved.status_code == 202, approved.text
    state = approved.json()
    assert state["status"] == "queued"
    review = state["options"]["product_identity_review"]
    assert review["status"] == "approved"
    assert review["checks"] == checks
    _, _, timeline_step = state["steps"]
    assert timeline_step["status"] == "queued"
    assert timeline_step["asset_id"] == review["output_asset_id"]
    timeline_job_id = timeline_step["job_id"]

    replay = client.post(
        f"/v1/workflows/{workflow_id}/identity-review",
        json={"approved": True, "notes": "تمت مراجعة هوية المنتج.", "checks": checks},
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["steps"][1]["job_id"] == timeline_job_id


def test_rejected_product_identity_blocks_all_timeline_and_export_jobs(
    tmp_path, monkeypatch
):
    client, workflow_id, _ = _create_identity_locked_workflow_at_review(
        tmp_path, monkeypatch, idempotency_key="identity-lock-review-reject-0001"
    )
    rejected = client.post(
        f"/v1/workflows/{workflow_id}/identity-review",
        json={
            "approved": False,
            "notes": "الشعار تغير وشكل المنتج لا يطابق الصورة المرجعية.",
        },
    )
    assert rejected.status_code == 202, rejected.text
    state = rejected.json()
    assert state["status"] == "failed"
    assert "rejected" in state["error_message"].lower()
    _, timeline_step, render_step = state["steps"]
    assert timeline_step["status"] == "cancelled"
    assert timeline_step["job_id"] is None
    assert render_step["status"] == "cancelled"
    assert render_step["job_id"] is None

