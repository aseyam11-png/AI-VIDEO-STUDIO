import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from app.api.routes import models as models_route
from app.core.config import settings
from app.integrations.comfyui import (
    ComfyUIError,
    build_workflow_prompt,
    validate_workflow_definition,
)
from app.integrations.ollama import OllamaError, _parse_plan
from app.media.pipeline import MediaJobCancelled
from app.media.speech import transcribe_local
from app.schemas.jobs import JobType
from app.schemas.models import CreativePlan
from app.services.jobs import JobRequestError, normalize_payload
from fastapi import HTTPException
from fastapi.responses import FileResponse


def _workflow():
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": "source.png"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "placeholder"}},
    }


def test_comfyui_bindings_only_change_allowlisted_inputs():
    workflow = _workflow()
    entry = {
        "required_inputs": ["source_path", "prompt"],
        "bindings": {
            "source_path": {"node_id": "1", "input_name": "image"},
            "prompt": {"node_id": "2", "input_name": "text"},
        },
    }

    result = build_workflow_prompt(
        workflow,
        entry,
        {"source_path": "/private/projects/source.png", "prompt": "A warm sunset"},
        "groundingdino_detect_v1",
    )

    assert result["1"]["inputs"]["image"] == "/private/projects/source.png"
    assert result["2"]["inputs"]["text"] == "A warm sunset"
    assert workflow["2"]["inputs"]["text"] == "placeholder"


def test_comfyui_rejects_unknown_workflow_and_node_binding():
    with pytest.raises(ComfyUIError):
        build_workflow_prompt(_workflow(), {"bindings": {}}, {}, "arbitrary_workflow")

    entry = {
        "required_inputs": ["prompt"],
        "bindings": {"prompt": {"node_id": "missing", "input_name": "text"}},
    }
    with pytest.raises(ComfyUIError):
        build_workflow_prompt(_workflow(), entry, {"prompt": "test"}, "groundingdino_detect_v1")


def test_comfyui_rejects_unapproved_binding_key():
    entry = {
        "required_inputs": ["shell"],
        "bindings": {"shell": {"node_id": "1", "input_name": "image"}},
    }
    with pytest.raises(ComfyUIError):
        build_workflow_prompt(_workflow(), entry, {"shell": "whoami"}, "groundingdino_detect_v1")


def test_qwen_plan_schema_checks_scene_durations():
    plan = {
        "title": "اختبار",
        "logline": "فيديو تجريبي",
        "target_duration_seconds": 15,
        "audience": "الجمهور العام",
        "visual_style": "واقعي سينمائي",
        "color_palette": ["#111111", "#eeeeee"],
        "audio_direction": "موسيقى هادئة",
        "scenes": [
            {
                "index": 1,
                "duration_seconds": 10,
                "purpose": "مقدمة",
                "visual_description": "لقطة افتتاحية",
                "shot_type": "wide",
                "camera_movement": "slow push",
                "narration": "",
                "on_screen_text": "",
                "sound_design": "soft ambience",
            },
            {
                "index": 2,
                "duration_seconds": 5,
                "purpose": "خاتمة",
                "visual_description": "لقطة ختامية",
                "shot_type": "close-up",
                "camera_movement": "static",
                "narration": "",
                "on_screen_text": "",
                "sound_design": "soft fade",
            },
        ],
        "production_notes": [],
    }
    parsed = _parse_plan(json.dumps(plan, ensure_ascii=False), 15)
    assert isinstance(parsed, CreativePlan)
    assert len(parsed.scenes) == 2


def test_qwen_plan_rejects_wrong_duration():
    content = json.dumps(
        {
            "title": "test",
            "logline": "test logline",
            "target_duration_seconds": 15,
            "audience": "general",
            "visual_style": "cinematic",
            "color_palette": ["black", "white"],
            "audio_direction": "soft music",
            "scenes": [
                {
                    "index": 1,
                    "duration_seconds": 15,
                    "purpose": "intro",
                    "visual_description": "opening",
                    "shot_type": "wide",
                    "camera_movement": "static",
                }
            ],
        }
    )
    with pytest.raises(OllamaError):
        _parse_plan(content, 30)


def test_comfyui_workflow_requires_real_file_and_valid_bindings(tmp_path, monkeypatch):
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "source.png"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "placeholder"}},
    }
    registry = {
        "version": 1,
        "workflows": {
            "groundingdino_detect_v1": {
                "file": "groundingdino_detect_v1.json",
                "required_inputs": ["source_path", "prompt"],
                "bindings": {
                    "source_path": {"node_id": "1", "input_name": "image"},
                    "prompt": {"node_id": "2", "input_name": "text"},
                },
            }
        },
    }
    (tmp_path / "groundingdino_detect_v1.json").write_text(
        json.dumps(workflow), encoding="utf-8"
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(settings, "model_workflow_registry", str(registry_path))
    monkeypatch.setattr(settings, "comfyui_base_url", "http://127.0.0.1:8188")

    assert validate_workflow_definition("groundingdino_detect_v1") is True


def test_comfyui_workflow_status_rejects_placeholder_node_ids(tmp_path, monkeypatch):
    workflow = {"1": {"class_type": "LoadImage", "inputs": {"image": "source.png"}}}
    registry = {
        "version": 1,
        "workflows": {
            "groundingdino_detect_v1": {
                "file": "groundingdino_detect_v1.json",
                "required_inputs": ["source_path"],
                "bindings": {
                    "source_path": {
                        "node_id": "REPLACE_NODE_ID",
                        "input_name": "REPLACE_INPUT_NAME",
                    }
                },
            }
        },
    }
    (tmp_path / "groundingdino_detect_v1.json").write_text(
        json.dumps(workflow), encoding="utf-8"
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(settings, "model_workflow_registry", str(registry_path))
    monkeypatch.setattr(settings, "comfyui_base_url", "http://127.0.0.1:8188")

    with pytest.raises(ComfyUIError):
        validate_workflow_definition("groundingdino_detect_v1")


def test_speech_job_is_disabled_until_optional_worker_is_enabled(monkeypatch):
    monkeypatch.setattr(settings, "speech_worker_enabled", False)
    with pytest.raises(JobRequestError, match="WhisperX worker is disabled"):
        normalize_payload(JobType.SPEECH_TRANSCRIBE, {})

    monkeypatch.setattr(settings, "speech_worker_enabled", True)
    payload = normalize_payload(JobType.SPEECH_TRANSCRIBE, {})
    assert payload == {"model_name": "small", "language": "ar", "align": False}


def test_deepfilter_job_requires_explicit_local_script_and_checkpoint(monkeypatch):
    monkeypatch.setattr(settings, "deepfilter_script", None)
    monkeypatch.setattr(settings, "deepfilter_model_dir", None)
    with pytest.raises(JobRequestError, match="DeepFilterNet is not configured"):
        normalize_payload(JobType.AUDIO_ENHANCE, {})

    monkeypatch.setattr(settings, "deepfilter_script", "/models/deepfilter/enhance.py")
    monkeypatch.setattr(settings, "deepfilter_model_dir", "/models/deepfilter/checkpoint")
    assert normalize_payload(JobType.AUDIO_ENHANCE, {}) == {"post_filter": False}


def test_wan_generation_requires_approval_and_stays_within_ten_seconds(monkeypatch):
    monkeypatch.setattr(settings, "comfyui_base_url", "http://127.0.0.1:8188")
    with pytest.raises(JobRequestError, match="explicit user approval"):
        normalize_payload(JobType.VIDEO_GENERATE, {"prompt": "A cinematic shot"})

    with pytest.raises(JobRequestError):
        normalize_payload(
            JobType.VIDEO_GENERATE,
            {"prompt": "A cinematic shot", "duration_seconds": 11, "approved": True},
        )

    payload = normalize_payload(
        JobType.VIDEO_GENERATE,
        {"prompt": "A cinematic shot", "duration_seconds": 5, "approved": True},
    )
    assert payload["duration_seconds"] == 5
    assert payload["approved"] is True


def test_model_job_queue_routing_is_separate_from_cpu_queue():
    from app.services.jobs import _MODEL_QUEUE_TYPES, _SPEECH_QUEUE_TYPES

    assert JobType.SPEECH_TRANSCRIBE in _SPEECH_QUEUE_TYPES
    assert JobType.VISION_DETECT in _MODEL_QUEUE_TYPES
    assert JobType.VIDEO_GENERATE in _MODEL_QUEUE_TYPES
    assert JobType.VIDEO_GENERATE_SEQUENCE in _MODEL_QUEUE_TYPES
    assert JobType.VIDEO_ENHANCE in _MODEL_QUEUE_TYPES
    assert JobType.MOTION_BLENDER in _MODEL_QUEUE_TYPES
    assert JobType.SPEECH_TRANSCRIBE not in _MODEL_QUEUE_TYPES
    assert JobType.AUDIO_ENHANCE not in _MODEL_QUEUE_TYPES


def test_model_output_download_uses_only_recorded_completed_job_file(tmp_path, monkeypatch):
    project_id = "00000000-0000-4000-8000-000000000001"
    job_id = "00000000-0000-4000-8000-000000000002"
    output_dir = tmp_path / "exports"
    output_dir.mkdir()
    output = output_dir / "cleaned.mp4"
    output.write_bytes(b"synthetic-video")
    job = {
        "id": job_id,
        "project_id": project_id,
        "status": "succeeded",
        "result": {"relative_path": "exports/cleaned.mp4"},
    }
    monkeypatch.setattr(models_route, "get_job", lambda requested_id: job)
    monkeypatch.setattr(models_route, "project_dir", lambda requested_project: tmp_path)

    response = models_route.download_job_output(project_id, job_id, "cleaned.mp4")

    assert isinstance(response, FileResponse)
    assert Path(response.path) == output


def test_model_output_download_rejects_cross_project_and_unknown_files(tmp_path, monkeypatch):
    project_id = "00000000-0000-4000-8000-000000000001"
    job_id = "00000000-0000-4000-8000-000000000002"
    job = {
        "id": job_id,
        "project_id": project_id,
        "status": "succeeded",
        "result": {"relative_path": "exports/cleaned.mp4"},
    }
    monkeypatch.setattr(models_route, "get_job", lambda requested_id: job)
    monkeypatch.setattr(models_route, "project_dir", lambda requested_project: tmp_path)

    with pytest.raises(HTTPException) as cross_project:
        models_route.download_job_output(
            "00000000-0000-4000-8000-000000000009", job_id, "cleaned.mp4"
        )
    assert cross_project.value.status_code == 404

    with pytest.raises(HTTPException) as unknown_file:
        models_route.download_job_output(project_id, job_id, "not-in-job.mp4")
    assert unknown_file.value.status_code == 404


def test_model_output_download_rejects_traversal_filename():
    with pytest.raises(HTTPException) as invalid:
        models_route.download_job_output(
            "00000000-0000-4000-8000-000000000001",
            "00000000-0000-4000-8000-000000000002",
            "../secret.txt",
        )
    assert invalid.value.status_code == 400



def test_whisperx_adapter_loads_audio_array_and_writes_transcript(tmp_path, monkeypatch):
    calls = {}

    class FakeModel:
        def transcribe(self, audio, *, batch_size, language):
            calls["audio"] = audio
            calls["batch_size"] = batch_size
            calls["language"] = language
            return {
                "language": language,
                "segments": [{"start": 0.0, "end": 1.0, "text": "مرحبا"}],
            }

    fake = ModuleType("whisperx")
    fake.load_model = lambda *args, **kwargs: (
        calls.update({"model_args": args, "model_kwargs": kwargs}) or FakeModel()
    )
    fake.load_audio = lambda path: ["decoded-audio"]

    monkeypatch.setitem(sys.modules, "whisperx", fake)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"synthetic-source")
    destination = tmp_path / "transcription.json"
    cache = tmp_path / "cache"

    result = transcribe_local(
        source,
        destination,
        language="ar",
        model_name="small",
        device="cpu",
        cache_dir=cache,
        align=False,
    )

    assert calls["audio"] == ["decoded-audio"]
    assert calls["batch_size"] == 4
    assert calls["language"] == "ar"
    assert calls["model_kwargs"]["download_root"] == str(cache)
    assert result["text"] == "مرحبا"
    assert destination.is_file()


def test_whisperx_adapter_honors_cancellation_before_loading(tmp_path, monkeypatch):
    fake = ModuleType("whisperx")
    fake.load_model = lambda *args, **kwargs: pytest.fail("model must not load")
    fake.load_audio = lambda path: pytest.fail("audio must not load")
    monkeypatch.setitem(sys.modules, "whisperx", fake)

    with pytest.raises(MediaJobCancelled):
        transcribe_local(
            tmp_path / "source.mp4",
            tmp_path / "transcription.json",
            cache_dir=tmp_path / "cache",
            cancel_check=lambda: True,
        )


def test_system_capabilities_report_phase3_without_claiming_unverified_inference(monkeypatch):
    from app.main import capabilities as system_capabilities

    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama.test:11434")
    monkeypatch.setattr(settings, "comfyui_base_url", "http://comfyui.test:8188")

    result = system_capabilities()

    assert result["phase"] == "3"
    assert result["implemented_phases"] == ["0.5", "1", "2", "3"]
    assert result["workflow_engine"] == {
        "implemented": True,
        "execution_mode": "sequential_v1",
        "approval_required_before_execution": True,
        "general_branching_dag": False,
    }
    assert result["model_runtimes"]["ollama_configured"] is True
    assert result["model_runtimes"]["comfyui_configured"] is True
    assert result["model_runtimes"]["real_inference_verified"] is False
    assert result["video_generation_adapter"] is True
    assert result["video_generation_inference_verified"] is False
