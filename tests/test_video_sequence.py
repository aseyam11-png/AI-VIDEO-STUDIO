import io
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.core.config import settings
from app.main import app
from app.media.storage import (
    create_project,
    ingest_file_stream,
    project_dir,
    register_generated_video_asset,
)
from app.schemas.jobs import JobType
from app.schemas.models import CreativePlan
from app.schemas.workflows import WorkflowCreateRequest
from app.services import job_runner, jobs, model_jobs, workflow_engine
from app.services.job_runner import run_job_attempt
from fastapi.testclient import TestClient


def _plan() -> CreativePlan:
    return CreativePlan.model_validate(
        {
            "title": "إعلان منتج متعدد اللقطات",
            "logline": "إعلان قصير يعرض المنتج في عدة مشاهد مترابطة.",
            "target_duration_seconds": 4,
            "audience": "الجمهور المهتم بالمنتجات الراقية",
            "visual_style": "سينمائي واقعي بإضاءة ناعمة",
            "color_palette": ["أسود", "ذهبي"],
            "audio_direction": "موسيقى هادئة تضاف في مرحلة المزج",
            "scenes": [
                {
                    "index": 1,
                    "duration_seconds": 2,
                    "purpose": "تقديم المنتج",
                    "visual_description": "لقطة قريبة واضحة للمنتج على خلفية داكنة.",
                    "shot_type": "لقطة قريبة",
                    "camera_movement": "حركة بطيئة للأمام",
                    "narration": "",
                    "on_screen_text": "التفاصيل تصنع الفرق",
                    "sound_design": "موسيقى افتتاحية هادئة",
                },
                {
                    "index": 2,
                    "duration_seconds": 2,
                    "purpose": "إظهار تفاصيل المنتج",
                    "visual_description": "دوران سينمائي خفيف حول المنتج مع إبقاء شكله ثابتًا.",
                    "shot_type": "لقطة متوسطة",
                    "camera_movement": "حركة جانبية بطيئة",
                    "narration": "",
                    "on_screen_text": "مصمم بعناية",
                    "sound_design": "مؤثر انتقال ناعم",
                },
            ],
            "production_notes": [],
        }
    )


def _request(plan: CreativePlan) -> WorkflowCreateRequest:
    return WorkflowCreateRequest(
        project_id=uuid4(),
        asset_id=uuid4(),
        idempotency_key="multi-shot-workflow-0001",
        brief="أنشئ إعلانًا قصيرًا متعدد اللقطات لهذا المنتج بأسلوب سينمائي واضح.",
        language="ar",
        creative_plan=plan,
        generate_video=True,
        interpolate_generated_video=False,
    )


def _make_video(path: Path, duration: int = 2) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is required for real multi-shot assembly.")
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=320x240:rate=24:duration={duration}",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_multishot_payload_requires_approval_and_respects_scene_limits(monkeypatch):
    monkeypatch.setattr(settings, "comfyui_base_url", "http://comfyui.test:8188")
    payload = {
        "shots": [
            {"prompt": "Product close-up", "duration_seconds": 2},
            {"prompt": "Slow side movement", "duration_seconds": 2},
        ],
        "approved": True,
    }
    normalized = jobs.normalize_payload(JobType.VIDEO_GENERATE_SEQUENCE, payload)
    assert len(normalized["shots"]) == 2
    assert normalized["approved"] is True

    with pytest.raises(jobs.JobRequestError, match="explicit user approval"):
        jobs.normalize_payload(
            JobType.VIDEO_GENERATE_SEQUENCE,
            {**payload, "approved": False},
        )

    with pytest.raises(jobs.JobRequestError):
        jobs.normalize_payload(
            JobType.VIDEO_GENERATE_SEQUENCE,
            {
                **payload,
                "shots": [
                    {"prompt": "One", "duration_seconds": 10},
                    {"prompt": "Two", "duration_seconds": 10},
                    {"prompt": "Three", "duration_seconds": 10},
                    {"prompt": "Four", "duration_seconds": 10},
                    {"prompt": "Five", "duration_seconds": 10},
                    {"prompt": "Six", "duration_seconds": 10},
                    {"prompt": "Seven", "duration_seconds": 10},
                    {"prompt": "Eight", "duration_seconds": 10},
                    {"prompt": "Nine", "duration_seconds": 1},
                ],
            },
        )


def test_multishot_workflow_uses_scene_prompts_and_one_assembly_job():
    request = _request(_plan())
    project = SimpleNamespace(target_fps=24, target_resolution="640x360")
    specs = workflow_engine._step_specs("image", request, project)

    assert specs[0][0] == JobType.VIDEO_GENERATE_SEQUENCE
    sequence = specs[0][1]
    assert len(sequence["shots"]) == 2
    assert sequence["shots"][0]["duration_seconds"] == 2
    assert "لقطة قريبة" in sequence["shots"][0]["prompt"]
    assert sequence["output_width"] == 640
    assert sequence["output_height"] == 360
    assert sequence["interpolate_factor"] == 1
    assert [item[0] for item in specs] == [
        JobType.VIDEO_GENERATE_SEQUENCE,
        JobType.MEDIA_TIMELINE,
        JobType.MEDIA_RENDER,
    ]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is required")
def test_approved_multishot_workflow_routes_final_asset_to_timeline_step(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    monkeypatch.setattr(settings, "comfyui_base_url", "http://comfyui.test:8188")
    project = create_project(
        {
            "name": "Multi-shot workflow chain",
            "target_duration_seconds": 4,
            "target_resolution": "640x360",
            "target_fps": 24,
            "language": "ar",
            "brief": "اختبار تمرير الفيديو الناتج إلى الخط الزمني.",
        }
    )
    image_asset, _ = ingest_file_stream(
        project["id"],
        "reference.png",
        io.BytesIO(b"synthetic-image-reference"),
        max_bytes=1024,
    )
    fixture = tmp_path / "workflow-sequence.mp4"
    _make_video(fixture, duration=4)

    def fake_sequence(job, source, *, progress, cancel_check):
        destination = project_dir(job["project_id"]) / "exports" / "workflow-sequence.mp4"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fixture, destination)
        asset = register_generated_video_asset(
            job["project_id"],
            job["id"],
            destination,
            max_bytes=settings.max_model_output_bytes,
        )
        return {"output_asset_id": asset["id"], "output_asset": asset}

    monkeypatch.setattr(job_runner, "execute_video_sequence", fake_sequence)
    client = TestClient(app)
    created = client.post(
        "/v1/workflows",
        json={
            "project_id": project["id"],
            "asset_id": image_asset["id"],
            "idempotency_key": "multi-shot-workflow-chain-0001",
            "brief": "أنشئ إعلانًا متعدد اللقطات من صورة المنتج ثم جهّزه للمونتاج.",
            "language": "ar",
            "creative_plan": _plan().model_dump(),
            "generate_video": True,
        },
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    assert [step["tool_id"] for step in created.json()["steps"]] == [
        "video.generate_sequence",
        "media.timeline",
        "media.render",
    ]

    approved = client.post(f"/v1/workflows/{run_id}/approve")
    assert approved.status_code == 202, approved.text
    first_step = approved.json()["steps"][0]
    job = client.get(f"/v1/jobs/{first_step['job_id']}").json()
    completed = run_job_attempt(job["id"], job["dispatch_generation"])
    assert completed["status"] == "succeeded"

    advanced = client.get(f"/v1/workflows/{run_id}").json()
    sequence_step, timeline_step, _ = advanced["steps"]
    assert sequence_step["status"] == "succeeded"
    assert sequence_step["result"]["output_asset_id"]
    assert timeline_step["status"] == "queued"
    assert timeline_step["asset_id"] == sequence_step["result"]["output_asset_id"]


def test_video_sequence_executes_wan_shots_registers_assets_and_assembles_mp4(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    project = create_project(
        {
            "name": "Multi-shot test",
            "target_duration_seconds": 4,
            "target_resolution": "640x360",
            "target_fps": 24,
            "language": "ar",
            "brief": "اختبار توليد وتجميع لقطتين متتاليتين.",
        }
    )
    image_asset, reference = ingest_file_stream(
        project["id"],
        "reference.png",
        io.BytesIO(b"synthetic-image-reference"),
        max_bytes=1024,
    )
    fixture = tmp_path / "generated-shot.mp4"
    _make_video(fixture, duration=2)
    calls = []

    class FakeComfyUIClient:
        def run_workflow(self, workflow_id, values, output_dir, *, progress, cancel_check):
            calls.append((workflow_id, values))
            assert "Canonical product identity lock" in values["prompt"]
            assert cancel_check() is False
            output_dir.mkdir(parents=True, exist_ok=True)
            output = output_dir / f"generated-{len(calls)}.mp4"
            shutil.copyfile(fixture, output)
            progress(90, "fake_output", "synthetic test output")
            return {"outputs": [{"saved_as": output.name}]}

    monkeypatch.setattr(model_jobs, "ComfyUIClient", FakeComfyUIClient)
    job_id = str(uuid4())
    job = {
        "id": job_id,
        "project_id": project["id"],
        "asset_id": image_asset["id"],
        "payload": {
            "shots": [
                {"prompt": "Product close-up", "negative_prompt": "", "duration_seconds": 2},
                {"prompt": "Slow side movement", "negative_prompt": "", "duration_seconds": 2},
            ],
            "generation_width": 832,
            "generation_height": 480,
            "output_width": 320,
            "output_height": 240,
            "fps": 24,
            "crf": 28,
            "interpolate_factor": 1,
            "approved": True,
            "product_identity_lock": True,
            "identity_reference_sha256": image_asset["sha256"],
        },
    }
    progress_events = []
    result = model_jobs.execute_video_sequence(
        job,
        reference,
        progress=lambda value, stage, message: progress_events.append((value, stage, message)),
        cancel_check=lambda: False,
    )

    assert [item[0] for item in calls] == [
        "wan22_ti2v_5b_v1",
        "wan22_ti2v_5b_v1",
    ]
    assert all(item[1]["source_path"] for item in calls)
    assert result["shot_count"] == 2
    assert result["duration_seconds"] == pytest.approx(4.0)
    assert result["output_asset_id"]
    assert len(result["shots"]) == 2
    assert Path(project_dir(project["id"]) / result["relative_path"]).is_file()
    sequence_manifest = Path(project_dir(project["id"]) / result["sequence_manifest_path"])
    assert sequence_manifest.is_file()
    manifest = json.loads(sequence_manifest.read_text(encoding="utf-8"))
    assert manifest["product_identity_lock"] is True
    assert manifest["identity_reference"]["asset_id"] == image_asset["id"]
    assert manifest["identity_reference"]["sha256"] == image_asset["sha256"]
    assert manifest["identity_reference"]["integrity_verified"] is True
    assert manifest["identity_reference"]["human_review_required"] is True
    assert all(
        shot["identity_reference_sha256"] == image_asset["sha256"]
        for shot in manifest["shots"]
    )
    assert progress_events[-1][0] == 96


def test_product_identity_reference_fingerprint_is_verified_before_wan_jobs(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    project = create_project(
        {
            "name": "Tampered product reference",
            "target_duration_seconds": 4,
            "target_resolution": "640x360",
            "target_fps": 24,
            "language": "ar",
            "brief": "اختبار سلامة مرجع المنتج قبل توليد اللقطات.",
        }
    )
    image_asset, reference = ingest_file_stream(
        project["id"],
        "reference.png",
        io.BytesIO(b"original-product-reference"),
        max_bytes=1024,
    )
    reference.write_bytes(b"tampered-product-reference")
    calls = []

    class FakeComfyUIClient:
        def run_workflow(self, *args, **kwargs):
            calls.append(args)
            raise AssertionError("Wan must not run with a tampered identity reference.")

    monkeypatch.setattr(model_jobs, "ComfyUIClient", FakeComfyUIClient)
    job = {
        "id": str(uuid4()),
        "project_id": project["id"],
        "asset_id": image_asset["id"],
        "payload": {
            "shots": [
                {"prompt": "Identity locked shot one", "negative_prompt": "", "duration_seconds": 2},
                {"prompt": "Identity locked shot two", "negative_prompt": "", "duration_seconds": 2},
            ],
            "generation_width": 832,
            "generation_height": 480,
            "output_width": 320,
            "output_height": 240,
            "fps": 24,
            "crf": 28,
            "interpolate_factor": 1,
            "approved": True,
            "product_identity_lock": True,
            "identity_reference_sha256": image_asset["sha256"],
        },
    }
    with pytest.raises(ValueError, match="SHA-256 integrity verification"):
        model_jobs.execute_video_sequence(
            job,
            reference,
            progress=lambda *_: None,
            cancel_check=lambda: False,
        )
    assert calls == []
