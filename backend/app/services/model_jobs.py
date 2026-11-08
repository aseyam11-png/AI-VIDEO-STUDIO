from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID

from app.core.config import settings
from app.integrations.blender import render_project_template
from app.integrations.comfyui import ComfyUIClient, ComfyUIError
from app.integrations.ffmpeg import MediaToolError, probe_media
from app.media.audio import enhance_video_audio
from app.media.speech import transcribe_local
from app.media.storage import atomic_write_json, project_dir


WORKFLOW_BY_JOB_TYPE = {
    "vision.detect": "groundingdino_detect_v1",
    "vision.track": "sam2_track_v1",
    "video.generate": "wan22_ti2v_5b_v1",
    "video.upscale": "realesrgan_upscale_v1",
    "video.interpolate": "rife_interpolate_v1",
}


def execute_speech_transcription(
    project_id: str,
    asset_id: str,
    job_id: str,
    source: Path,
    payload: dict,
    *,
    cancel_check,
) -> dict:
    destination = (
        project_dir(project_id) / "transcriptions" / f"{asset_id}-{job_id}.json"
    )
    if cancel_check():
        raise RuntimeError("Transcription was cancelled before model loading.")
    result = transcribe_local(
        source,
        destination,
        language=payload["language"],
        model_name=payload["model_name"],
        device="cpu",
        cache_dir=Path(settings.media_root).expanduser().resolve() / "model-cache" / "whisperx",
        align=payload["align"],
        cancel_check=cancel_check,
    )
    relative_path = destination.relative_to(project_dir(project_id)).as_posix()
    return {
        "path": relative_path,
        "relative_path": relative_path,
        "download_url": (
            f"/v1/projects/{project_id}/jobs/{job_id}/files/"
            f"{quote(destination.name, safe='')}"
        ),
        **result,
    }


def execute_audio_enhancement(
    project_id: str,
    asset_id: str,
    job_id: str,
    source: Path,
    payload: dict,
    *,
    cancel_check,
) -> dict:
    destination = (
        project_dir(project_id) / "exports" / f"{asset_id}-{job_id}-audio-cleaned.mp4"
    )
    result = enhance_video_audio(
        source,
        destination,
        post_filter=payload["post_filter"],
        cancel_check=cancel_check,
    )
    relative_path = destination.relative_to(project_dir(project_id)).as_posix()
    return {
        **result,
        "relative_path": relative_path,
        "download_url": (
            f"/v1/projects/{project_id}/jobs/{job_id}/files/"
            f"{quote(destination.name, safe='')}"
        ),
    }


def execute_comfyui_job(
    job: dict[str, Any],
    source: Path,
    *,
    progress,
    cancel_check,
) -> dict:
    workflow_id = WORKFLOW_BY_JOB_TYPE.get(job["job_type"])
    if not workflow_id:
        raise ValueError("No approved ComfyUI workflow is mapped to this job type.")
    output_dir = project_dir(job["project_id"]) / "ai_outputs" / str(UUID(job["id"]))
    media_root = Path(settings.media_root).expanduser().resolve()
    try:
        relative_source = source.resolve().relative_to(media_root).as_posix()
    except ValueError as exc:
        raise ValueError("ComfyUI input must remain inside the configured MEDIA_ROOT.") from exc
    inputs = {"source_path": relative_source, **job["payload"]}
    result = ComfyUIClient().run_workflow(
        workflow_id,
        inputs,
        output_dir,
        progress=progress,
        cancel_check=cancel_check,
    )
    if not result["outputs"]:
        raise ComfyUIError(f"ComfyUI workflow {workflow_id} produced no downloadable media.")
    video_stream_found = False
    for output in result["outputs"]:
        output_path = output_dir / output["saved_as"]
        try:
            metadata = probe_media(output_path)
        except (MediaToolError, FileNotFoundError) as exc:
            raise ComfyUIError(
                f"ComfyUI output {output['saved_as']} failed media probing: {exc}"
            ) from exc
        streams = metadata.get("streams", [])
        if not streams:
            raise ComfyUIError(f"ComfyUI output {output['saved_as']} has no valid media stream.")
        output["metadata"] = metadata
        if (
            output_path.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv", ".gif"}
            and any(stream.get("codec_type") == "video" for stream in streams)
        ):
            video_stream_found = True
        output["download_url"] = (
            f"/v1/projects/{job['project_id']}/jobs/{job['id']}/files/"
            f"{quote(output['saved_as'], safe='')}"
        )
    if job["job_type"] in {"video.generate", "video.interpolate"} and not video_stream_found:
        raise ComfyUIError(f"ComfyUI workflow {workflow_id} did not produce a video file.")
    result["files_directory"] = output_dir.relative_to(project_dir(job["project_id"])).as_posix()
    atomic_write_json(output_dir / "result.json", result)
    return result


def execute_blender_job(
    job: dict[str, Any],
    *,
    cancel_check,
) -> dict:
    payload = job["payload"]
    destination = (
        project_dir(job["project_id"]) / "exports" / f"{job['asset_id']}-{job['id']}-motion.mp4"
    )
    result = render_project_template(
        job["project_id"],
        payload["template_id"],
        destination,
        frame_start=payload["frame_start"],
        frame_end=payload["frame_end"],
        width=payload["width"],
        height=payload["height"],
        fps=payload["fps"],
        cancel_check=cancel_check,
    )
    result["relative_path"] = destination.relative_to(project_dir(job["project_id"])).as_posix()
    result["download_url"] = (
        f"/v1/projects/{job['project_id']}/jobs/{job['id']}/files/{quote(destination.name, safe='')}"
    )
    return result
