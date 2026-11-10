import io
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from app.core.config import settings
from app.media import pipeline
from app.media.storage import (
    create_project,
    ingest_file_stream,
    project_dir,
    update_asset_manifest,
)
from app.schemas.jobs import JobType
from app.schemas.models import CreativePlan
from app.schemas.workflows import WorkflowCreateRequest
from app.services import jobs, model_jobs, workflow_engine


def _make_source_video(path: Path, duration: int = 1, fps: int = 24) -> None:
    binary = shutil.which("ffmpeg")
    if not binary:
        pytest.skip("FFmpeg is required for video enhancement integration tests.")
    subprocess.run(
        [
            binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=320x240:rate={fps}:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _project_with_video(tmp_path, monkeypatch, *, duration=1):
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    project = create_project(
        {
            "name": "Video enhancement acceptance",
            "target_duration_seconds": duration,
            "target_resolution": "640x480",
            "target_fps": 24,
            "language": "ar",
            "brief": "اختبار رفع دقة الفيديو مع الحفاظ على الصوت.",
        }
    )
    fixture = tmp_path / "source.mp4"
    _make_source_video(fixture, duration=duration)
    asset, source = ingest_file_stream(
        project["id"],
        "source.mp4",
        io.BytesIO(fixture.read_bytes()),
        max_bytes=10 * 1024 * 1024,
    )
    metadata = pipeline.probe_media(source)
    update_asset_manifest(project["id"], asset["id"], {"probe": metadata, "status": "ready"})
    return project, asset, source


def _plan(duration=4):
    return CreativePlan.model_validate(
        {
            "title": "فيديو قصير محسّن",
            "logline": "تحسين وضوح فيديو قصير مع الحفاظ على الصوت الأصلي.",
            "target_duration_seconds": duration,
            "audience": "المشاهدون",
            "visual_style": "واقعي وواضح",
            "color_palette": ["أسود", "أبيض"],
            "audio_direction": "الحفاظ على الصوت الأصلي",
            "scenes": [
                {
                    "index": 1,
                    "duration_seconds": duration,
                    "purpose": "عرض المشهد",
                    "visual_description": "تفاصيل واضحة في مشهد قصير.",
                    "shot_type": "لقطة متوسطة",
                    "camera_movement": "ثابت",
                    "narration": "",
                    "on_screen_text": "",
                    "sound_design": "الصوت الأصلي",
                }
            ],
            "production_notes": [],
        }
    )


def test_video_enhancement_requires_approval_and_bounded_video_payload(monkeypatch):
    monkeypatch.setattr(settings, "comfyui_base_url", "http://comfyui.test:8188")
    with pytest.raises(jobs.JobRequestError, match="explicit user approval"):
        jobs.normalize_payload(JobType.VIDEO_ENHANCE, {})

    payload = jobs.normalize_payload(
        JobType.VIDEO_ENHANCE,
        {"scale": 2, "fps": 24, "max_duration_seconds": 10, "approved": True},
    )
    assert payload["scale"] == 2
    assert payload["fps"] == 24
    assert payload["approved"] is True

    with pytest.raises(jobs.JobRequestError):
        jobs.normalize_payload(
            JobType.VIDEO_ENHANCE,
            {"scale": 2, "fps": 24, "max_duration_seconds": 11, "approved": True},
        )


def test_video_enhancement_job_is_models_queue_and_requires_video_asset():
    assert JobType.VIDEO_ENHANCE in jobs._MODEL_QUEUE_TYPES
    assert JobType.VIDEO_ENHANCE in jobs._VIDEO_REQUIRED_TYPES


def test_workflow_upscale_option_adds_video_enhancement_and_rejects_long_sources():
    project = type("Project", (), {"target_fps": 24, "target_resolution": "640x480"})()
    request = WorkflowCreateRequest(
        project_id=uuid4(),
        asset_id=uuid4(),
        idempotency_key="video-enhancement-workflow-01",
        brief="ارفع دقة هذا الفيديو القصير مع الحفاظ على الصوت.",
        creative_plan=_plan(),
        upscale_output=True,
    )
    steps = workflow_engine._step_specs(
        "video",
        request,
        project,
        asset_metadata={"probe": {"format": {"duration": "4.0"}}},
    )
    assert JobType.VIDEO_ENHANCE in [item[0] for item in steps]
    enhance_payload = next(payload for kind, payload in steps if kind == JobType.VIDEO_ENHANCE)
    assert enhance_payload["approved"] is True
    assert enhance_payload["scale"] == 2

    with pytest.raises(workflow_engine.WorkflowRequestError, match="10 seconds"):
        workflow_engine._step_specs(
            "video",
            request,
            project,
            asset_metadata={"probe": {"format": {"duration": "12.0"}}},
        )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is required")
def test_single_shot_generation_registers_video_asset_for_next_workflow_step(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    project = create_project(
        {
            "name": "Generated video handoff",
            "target_duration_seconds": 4,
            "target_resolution": "640x480",
            "target_fps": 24,
            "language": "ar",
            "brief": "توليد فيديو قصير لتسليمه إلى خطوة تحسين الفيديو.",
        }
    )
    reference_path = tmp_path / "reference.png"
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x240",
            "-frames:v",
            "1",
            str(reference_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    image_asset, source = ingest_file_stream(
        project["id"],
        "reference.png",
        io.BytesIO(reference_path.read_bytes()),
        max_bytes=2 * 1024 * 1024,
    )
    generated_fixture = tmp_path / "generated.mp4"
    _make_source_video(generated_fixture, duration=1)

    class FakeComfyUIClient:
        def run_workflow(
            self, workflow_id, values, output_dir, *, progress, cancel_check
        ):
            assert workflow_id == "wan22_ti2v_5b_v1"
            assert values["source_path"]
            output_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(generated_fixture, output_dir / "generated.mp4")
            progress(90, "generated", "synthetic generated video")
            return {"outputs": [{"saved_as": "generated.mp4"}]}

    monkeypatch.setattr(model_jobs, "ComfyUIClient", FakeComfyUIClient)
    job_id = str(uuid4())
    result = model_jobs.execute_comfyui_job(
        {
            "id": job_id,
            "project_id": project["id"],
            "asset_id": image_asset["id"],
            "job_type": JobType.VIDEO_GENERATE.value,
            "payload": {"prompt": "A short neutral scene", "approved": True},
        },
        source,
        progress=lambda *_: None,
        cancel_check=lambda: False,
    )
    assert result["output_asset_id"] == result["output_asset"]["id"]
    assert result["output_asset"]["asset_type"] == "video"
    registered_path = project_dir(project["id"]) / result["output_asset"]["relative_path"]
    assert registered_path.is_file()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is required")
def test_extract_and_assemble_upscaled_frames_preserves_audio(tmp_path):
    source = tmp_path / "source.mp4"
    _make_source_video(source, duration=1)
    extracted = tmp_path / "extracted"
    enhanced = tmp_path / "enhanced"
    metadata = pipeline.extract_video_frames(
        source=source,
        destination=extracted,
        fps=24,
        max_duration_seconds=1,
        max_frames=300,
    )
    assert metadata["frame_count"] == 24
    assert metadata["fps"] == 24

    binary = shutil.which("ffmpeg")
    enhanced.mkdir(parents=True)
    for index in range(1, metadata["frame_count"] + 1):
        input_frame = extracted / f"frame-{index:06d}.png"
        output_frame = enhanced / f"frame-{index:06d}.png"
        subprocess.run(
            [
                binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(input_frame),
                "-vf",
                "scale=640:480",
                str(output_frame),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

    destination = tmp_path / "exports" / "enhanced.mp4"
    result = pipeline.assemble_upscaled_video(
        source=source,
        frames_directory=enhanced,
        destination=destination,
        frame_count=metadata["frame_count"],
        fps=metadata["fps"],
        duration_seconds=metadata["source_duration_seconds"],
        expected_width=640,
        expected_height=480,
        crf=28,
    )
    assert destination.is_file()
    assert result["width"] == 640
    assert result["height"] == 480
    assert result["has_audio"] is True
    assert result["duration_seconds"] == pytest.approx(1, abs=0.15)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is required")
def test_extract_and_assemble_preserve_non_preset_source_fps(tmp_path):
    source = tmp_path / "source-20fps.mp4"
    _make_source_video(source, duration=1, fps=20)
    extracted = tmp_path / "extracted-20fps"
    enhanced = tmp_path / "enhanced-20fps"
    metadata = pipeline.extract_video_frames(
        source=source,
        destination=extracted,
        fps=24,
        max_duration_seconds=1,
    )
    assert metadata["fps"] == 20
    assert metadata["frame_count"] == 20

    binary = shutil.which("ffmpeg")
    enhanced.mkdir(parents=True)
    for index in range(1, metadata["frame_count"] + 1):
        subprocess.run(
            [
                binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(extracted / f"frame-{index:06d}.png"),
                "-vf",
                "scale=640:480",
                str(enhanced / f"frame-{index:06d}.png"),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

    destination = tmp_path / "exports" / "enhanced-20fps.mp4"
    result = pipeline.assemble_upscaled_video(
        source=source,
        frames_directory=enhanced,
        destination=destination,
        frame_count=metadata["frame_count"],
        fps=metadata["fps"],
        duration_seconds=metadata["source_duration_seconds"],
        expected_width=640,
        expected_height=480,
        crf=28,
    )
    assert result["fps"] == 20
    assert result["has_audio"] is True


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is required")
def test_video_enhancement_runs_comfyui_for_each_frame_and_registers_output(
    tmp_path, monkeypatch
):
    project, asset, source = _project_with_video(tmp_path, monkeypatch, duration=1)
    frame = tmp_path / "upscaled-frame.png"
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=640x480",
            "-frames:v",
            "1",
            str(frame),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    calls = []

    class FakeComfyUIClient:
        def run_workflow(self, workflow_id, values, output_dir, *, progress, cancel_check):
            calls.append((workflow_id, values))
            assert workflow_id == "realesrgan_upscale_v1"
            assert Path(settings.media_root, values["source_path"]).is_file()
            assert values["scale"] == 2
            if cancel_check():
                raise RuntimeError("cancelled")
            output_dir.mkdir(parents=True, exist_ok=True)
            filename = output_dir / "upscaled.png"
            shutil.copyfile(frame, filename)
            progress(90, "upscaled", "synthetic image output")
            return {"outputs": [{"saved_as": filename.name}]}

    monkeypatch.setattr(model_jobs, "ComfyUIClient", FakeComfyUIClient)
    job_id = str(uuid4())
    progress_events = []
    result = model_jobs.execute_video_enhancement(
        {
            "id": job_id,
            "project_id": project["id"],
            "asset_id": asset["id"],
            "payload": {
                "scale": 2,
                "fps": 24,
                "max_duration_seconds": 10,
                "crf": 28,
                "approved": True,
            },
        },
        source,
        progress=lambda value, stage, message: progress_events.append((value, stage, message)),
        cancel_check=lambda: False,
    )
    assert len(calls) == 24
    assert result["frame_count"] == 24
    assert result["output_width"] == 640
    assert result["output_height"] == 480
    assert result["audio_preserved"] is True
    assert result["output_asset_id"]
    assert (project_dir(project["id"]) / result["relative_path"]).is_file()
    assert (project_dir(project["id"]) / result["manifest_path"]).is_file()
    assert progress_events[-1][1] == "enhancement_complete"
