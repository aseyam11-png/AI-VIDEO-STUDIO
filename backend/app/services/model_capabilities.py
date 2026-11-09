import shutil
from pathlib import Path

from app.core.config import settings
from app.integrations.comfyui import (
    SUPPORTED_WORKFLOWS,
    ComfyUIClient,
    load_workflow_registry,
    validate_workflow_definition,
)
from app.integrations.ollama import installed_model_names


def _ollama_status() -> dict:
    if not settings.ollama_base_url:
        return {"configured": False, "reachable": False, "model_installed": False}
    try:
        names = installed_model_names()
        return {
            "configured": True,
            "reachable": True,
            "model": settings.ollama_model,
            "model_installed": settings.ollama_model in names,
        }
    except Exception as exc:
        return {
            "configured": True,
            "reachable": False,
            "model": settings.ollama_model,
            "error": str(exc)[:300],
        }


def _comfyui_status() -> dict:
    if not settings.comfyui_base_url:
        return {"configured": False, "reachable": False, "workflows": {}}
    try:
        client = ComfyUIClient()
        client.health_check()
        object_info = client._request("/object_info")
        if not isinstance(object_info, dict):
            raise RuntimeError("ComfyUI did not return its installed node catalog.")
        registry = load_workflow_registry()["workflows"]
        workflows = {}
        for workflow_id in SUPPORTED_WORKFLOWS:
            entry = registry.get(workflow_id)
            try:
                ready = isinstance(entry, dict) and validate_workflow_definition(
                    workflow_id, object_info=object_info
                )
            except Exception:
                ready = False
            workflows[workflow_id] = {
                "registered": isinstance(entry, dict),
                "workflow_file_ready": ready,
                "bindings_valid": ready,
                "required_nodes_and_model_choices_present": ready,
                "ready": False,
                "inference_verified": False,
            }
        return {"configured": True, "reachable": True, "workflows": workflows}
    except Exception as exc:
        return {
            "configured": True,
            "reachable": False,
            "workflows": {},
            "error": str(exc)[:300],
        }


def _path_exists(value: str | None, *, directory: bool = False) -> bool:
    if not value:
        return False
    path = Path(value).expanduser()
    return path.is_dir() if directory else path.is_file()


def get_model_capabilities() -> dict:
    whisperx_configured = settings.speech_worker_enabled
    deepfilter_ready = (
        _path_exists(settings.deepfilter_script)
        and _path_exists(settings.deepfilter_model_dir, directory=True)
    )
    blender_executable = settings.blender_executable or shutil.which("blender")
    comfyui = _comfyui_status()
    ollama = _ollama_status()
    workflows = comfyui.get("workflows", {})

    def workflow_configured(workflow_id: str) -> bool:
        return bool(
            workflows.get(workflow_id, {}).get(
                "required_nodes_and_model_choices_present"
            )
        )

    return {
        "phase": "3",
        "workflow_engine": {
            "implemented": True,
            "execution_mode": "sequential_v1",
            "approval_required_before_execution": True,
            "general_branching_dag": False,
        },
        "weight_policy": "No model weights are downloaded by application startup or CI.",
        "runtimes": {"ollama": ollama, "comfyui": comfyui},
        "models": {
            "qwen3.5-9b-q4_K_M": {
                "role": "Arabic creative director and structured planning",
                "configured": bool(settings.ollama_base_url),
                "model_installed": bool(ollama.get("model_installed")),
                "ready": bool(ollama.get("reachable") and ollama.get("model_installed")),
                "inference_verified": False,
            },
            "whisperx": {
                "role": "Speech transcription and optional alignment",
                "ready": False,
                "configured": whisperx_configured,
                "worker_enabled": whisperx_configured,
                "default_profile": "small",
                "weights_loaded": False,
                "readiness_note": (
                    "The API cannot observe the private CPU worker's Python environment or "
                    "model warm state; a successful transcription job is the runtime proof."
                ),
            },
            "grounding-dino": {
                "role": "Text-conditioned object detection",
                "ready": False,
                "workflow_configured": workflow_configured("groundingdino_detect_v1"),
            },
            "sam2": {
                "role": "Prompted segmentation and video tracking",
                "ready": False,
                "workflow_configured": workflow_configured("sam2_track_v1"),
            },
            "wan-2.2-ti2v-5b": {
                "role": "Approved image/text-to-video shot generation",
                "ready": False,
                "workflow_configured": workflow_configured("wan22_ti2v_5b_v1"),
            },
            "deepfilternet": {
                "role": "Speech denoising",
                "ready": False,
                "configured": deepfilter_ready,
                "weights_present": _path_exists(settings.deepfilter_model_dir, directory=True),
            },
            "realesrgan": {
                "role": "Selected-frame/image upscaling",
                "ready": False,
                "workflow_configured": workflow_configured("realesrgan_upscale_v1"),
            },
            "rife": {
                "role": "Video frame interpolation",
                "ready": False,
                "workflow_configured": workflow_configured("rife_interpolate_v1"),
            },
            "blender": {
                "role": "Deterministic motion graphics and compositing renders",
                "ready": False,
                "executable_configured": bool(blender_executable),
                "readiness_note": (
                    "The API cannot verify the executable inside the dedicated model worker; "
                    "a successful Blender render job is the runtime proof."
                ),
            },
        },
        "tool_placement": {
            "director": ["qwen3.5-9b-q4_K_M", "langgraph"],
            "speech": ["whisperx"],
            "visual_understanding": ["grounding-dino", "sam2"],
            "generation": ["comfyui", "wan-2.2-ti2v-5b"],
            "audio": ["deepfilternet"],
            "motion_graphics": ["blender"],
            "restoration": ["realesrgan", "rife"],
            "timeline_and_export": ["opentimelineio", "ffmpeg"],
        },
    }
