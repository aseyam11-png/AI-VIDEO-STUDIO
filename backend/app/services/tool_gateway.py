import typing  # noqa: I001

from app.schemas.jobs import JobType
from app.schemas.tools import DirectorPlanToolCall
from app.services.jobs import _OPTION_MODELS
from app.services.model_capabilities import get_model_capabilities


TOOL_SPECS: tuple[dict[str, typing.Any], ...] = (
    {
        "tool_id": "director.plan",
        "name": "AI Director",
        "category": "planning",
        "runtime": "Ollama / Qwen3.5-9B",
        "description": "Create a validated Arabic or English creative plan; no jobs are started.",
        "execution": "synchronous_draft",
        "approval_required": True,
        "workflow_id": None,
    },
    {
        "tool_id": "media.proxy",
        "name": "Video Proxy",
        "category": "media",
        "runtime": "FFmpeg",
        "description": "Create a lightweight editing proxy from a video asset.",
        "execution": "durable_job",
        "approval_required": False,
    },
    {
        "tool_id": "media.scenes",
        "name": "Scene Detection",
        "category": "media",
        "runtime": "PySceneDetect / FFmpeg",
        "description": "Detect candidate scene boundaries in a video.",
        "execution": "durable_job",
        "approval_required": False,
    },
    {
        "tool_id": "media.timeline",
        "name": "Timeline Assembly",
        "category": "editing",
        "runtime": "OpenTimelineIO",
        "description": "Build an editable timeline manifest from detected scenes.",
        "execution": "durable_job",
        "approval_required": False,
    },
    {
        "tool_id": "media.render",
        "name": "Video Render",
        "category": "editing",
        "runtime": "FFmpeg",
        "description": "Render the project's saved timeline to a verified video output.",
        "execution": "durable_job",
        "approval_required": False,
    },
    {
        "tool_id": "speech.transcribe",
        "name": "Speech Transcription",
        "category": "speech",
        "runtime": "WhisperX / speech worker",
        "description": "Transcribe Arabic or English speech and optionally align words.",
        "execution": "durable_job",
        "approval_required": False,
    },
    {
        "tool_id": "audio.enhance",
        "name": "Audio Denoising",
        "category": "audio",
        "runtime": "DeepFilterNet / CPU worker",
        "description": "Denoise source speech and mux the enhanced audio into a new video.",
        "execution": "durable_job",
        "approval_required": False,
    },
    {
        "tool_id": "vision.detect",
        "name": "Text-Guided Object Detection",
        "category": "vision",
        "runtime": "ComfyUI / Grounding DINO",
        "description": "Detect objects in an image or video using a text prompt.",
        "execution": "durable_job",
        "approval_required": False,
        "workflow_id": "groundingdino_detect_v1",
        "capability_key": "grounding-dino",
    },
    {
        "tool_id": "vision.track",
        "name": "Object Segmentation and Tracking",
        "category": "vision",
        "runtime": "ComfyUI / SAM 2",
        "description": "Segment and track a prompted object in a video.",
        "execution": "durable_job",
        "approval_required": False,
        "workflow_id": "sam2_track_v1",
        "capability_key": "sam2",
    },
    {
        "tool_id": "video.generate",
        "name": "Video Shot Generation",
        "category": "generation",
        "runtime": "ComfyUI / Wan 2.2 TI2V 5B",
        "description": "Generate a short shot from a prompt and image reference.",
        "execution": "durable_job",
        "approval_required": True,
        "workflow_id": "wan22_ti2v_5b_v1",
        "capability_key": "wan-2.2-ti2v-5b",
    },
    {
        "tool_id": "video.generate_sequence",
        "name": "Multi-Shot Video Generation",
        "category": "generation",
        "runtime": "ComfyUI / Wan 2.2 TI2V 5B + FFmpeg",
        "description": "Generate 2–8 approved shots from a project image reference and assemble them in order.",
        "execution": "durable_job",
        "approval_required": True,
        "workflow_id": "wan22_ti2v_5b_v1",
        "capability_key": "wan-2.2-ti2v-5b",
    },
    {
        "tool_id": "video.upscale",
        "name": "Image and Frame Upscaling",
        "category": "restoration",
        "runtime": "ComfyUI / Real-ESRGAN",
        "description": "Upscale a selected image or video frame.",
        "execution": "durable_job",
        "approval_required": False,
        "workflow_id": "realesrgan_upscale_v1",
        "capability_key": "realesrgan",
    },
    {
        "tool_id": "video.interpolate",
        "name": "Frame Interpolation",
        "category": "restoration",
        "runtime": "ComfyUI / RIFE",
        "description": "Generate intermediate frames for a video.",
        "execution": "durable_job",
        "approval_required": False,
        "workflow_id": "rife_interpolate_v1",
        "capability_key": "rife",
    },
    {
        "tool_id": "motion.blender",
        "name": "Motion Graphics Render",
        "category": "motion",
        "runtime": "Blender",
        "description": "Render a reviewed project template; arbitrary scripts are not accepted.",
        "execution": "durable_job",
        "approval_required": True,
    },
)


def _readiness(
    spec: dict[str, typing.Any], capabilities: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    tool_id = spec["tool_id"]
    models = capabilities.get("models", {})
    runtimes = capabilities.get("runtimes", {})

    if tool_id == "director.plan":
        model = models.get("qwen3.5-9b-q4_K_M", {})
        present = bool(model.get("ready"))
        return {
            "state": (
                "model_present_smoke_test_required"
                if present else "runtime_or_model_unavailable"
            ),
            "configured": bool(model.get("configured")),
            "inference_verified": False,
        }

    if tool_id == "speech.transcribe":
        enabled = bool(models.get("whisperx", {}).get("worker_enabled"))
        return {
            "state": "worker_enabled_first_use_model_required" if enabled else "disabled",
            "configured": enabled,
            "inference_verified": False,
        }

    if tool_id == "audio.enhance":
        configured = bool(models.get("deepfilternet", {}).get("configured"))
        return {
            "state": "configured_inference_unverified" if configured else "not_configured",
            "configured": configured,
            "inference_verified": False,
        }

    if tool_id == "motion.blender":
        configured = bool(models.get("blender", {}).get("executable_configured"))
        return {
            "state": (
                "executable_configured_project_template_required"
                if configured else "not_configured"
            ),
            "configured": configured,
            "inference_verified": False,
        }

    if spec.get("workflow_id"):
        runtime_reachable = bool(runtimes.get("comfyui", {}).get("reachable"))
        capability = models.get(spec.get("capability_key"), {})
        workflow_configured = bool(capability.get("workflow_configured"))
        configured = runtime_reachable and workflow_configured
        if configured:
            state = "workflow_preflight_passed_inference_unverified"
        elif not runtime_reachable:
            state = "comfyui_unavailable"
        else:
            state = "workflow_missing_or_invalid"
        return {
            "state": state,
            "configured": configured,
            "workflow_id": spec["workflow_id"],
            "inference_verified": False,
        }

    return {
        "state": "adapter_implemented_worker_execution_not_probed",
        "configured": True,
        "inference_verified": False,
    }


def get_tool_catalog() -> dict[str, typing.Any]:
    """Return the one public tool catalog, with honest runtime readiness boundaries."""
    capabilities = get_model_capabilities()
    tools: list[dict[str, typing.Any]] = []

    for spec in TOOL_SPECS:
        item = {
            **spec,
            "readiness": _readiness(spec, capabilities),
        }
        if spec["tool_id"] == "director.plan":
            item["input_schema"] = DirectorPlanToolCall.model_json_schema()
        else:
            job_type = JobType(spec["tool_id"])
            item["job_type"] = job_type.value
            item["payload_schema"] = _OPTION_MODELS[job_type].model_json_schema()
            item["queue"] = (
                "speech" if job_type == JobType.SPEECH_TRANSCRIBE
                else "models" if job_type in {
                    JobType.VISION_DETECT,
                    JobType.VISION_TRACK,
                    JobType.VIDEO_GENERATE,
                    JobType.VIDEO_GENERATE_SEQUENCE,
                    JobType.VIDEO_UPSCALE,
                    JobType.VIDEO_INTERPOLATE,
                    JobType.MOTION_BLENDER,
                }
                else "cpu"
            )
        tools.append(item)

    return {
        "version": "1.0",
        "name": "AI Video Studio Tool Gateway",
        "dispatch_endpoint": "/v1/tools/execute",
        "job_status_endpoint": "/v1/jobs/{job_id}",
        "job_events_endpoint": "/v1/jobs/{job_id}/events",
        "tools": tools,
        "execution_policy": {
            "long_running_operations": "durable_jobs",
            "idempotency_required": True,
            "user_approval_required_for": [
                "video.generate",
                "video.generate_sequence",
                "motion.blender",
            ],
            "model_weights_downloaded_automatically": False,
            "inference_verification": (
                "A successful real job is required; "
                "CI adapter tests are not inference proof."
            ),
        },
    }
