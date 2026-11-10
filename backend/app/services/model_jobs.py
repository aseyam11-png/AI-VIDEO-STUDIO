import json
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid5

from app.core.config import settings
from app.integrations.blender import render_project_template
from app.integrations.comfyui import ComfyUICancelled, ComfyUIClient, ComfyUIError
from app.integrations.ffmpeg import MediaToolError, probe_media
from app.media.audio import enhance_video_audio
from app.media.pipeline import (
    MediaJobCancelled,
    assemble_upscaled_video,
    extract_video_frames,
    render_multi_source_timeline,
)
from app.media.speech import transcribe_local
from app.media.storage import (
    asset_dir,
    atomic_write_json,
    project_dir,
    register_generated_video_asset,
)

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






def execute_video_enhancement(
    job: dict[str, Any],
    source: Path,
    *,
    progress,
    cancel_check,
) -> dict:
    """Upscale each extracted video frame with Real-ESRGAN and remux source audio."""
    project_id = str(UUID(job["project_id"]))
    job_id = str(UUID(job["id"]))
    payload = job["payload"]
    media_root = Path(settings.media_root).expanduser().resolve()
    try:
        source_relative = source.resolve().relative_to(media_root).as_posix()
    except ValueError as exc:
        raise ValueError("Video enhancement source must remain inside MEDIA_ROOT.") from exc

    output_root = project_dir(project_id) / "ai_outputs" / str(job_id)
    extracted_dir = output_root / "frames" / "source"
    enhanced_dir = output_root / "frames" / "enhanced"
    extracted_dir.mkdir(parents=True, exist_ok=True)
    enhanced_dir.mkdir(parents=True, exist_ok=True)

    progress(8, "extracting_frames", "Extracting a bounded frame sequence from the source video.")
    extraction = extract_video_frames(
        source=source,
        destination=extracted_dir,
        fps=payload["fps"],
        max_duration_seconds=payload["max_duration_seconds"],
        max_frames=300,
        cancel_check=cancel_check,
    )
    expected_width = extraction["source_width"] * payload["scale"]
    expected_height = extraction["source_height"] * payload["scale"]
    if expected_width > 7680 or expected_height > 4320:
        raise ValueError("Upscaled output would exceed the supported 8K dimensions.")

    client = ComfyUIClient()
    output_bytes = 0
    enhanced_frames: list[Path] = []
    frame_count = extraction["frame_count"]
    for index in range(1, frame_count + 1):
        if cancel_check():
            raise MediaJobCancelled("Video enhancement was cancelled between frame jobs.")
        source_frame = extracted_dir / f"frame-{index:06d}.png"
        frame_output_dir = output_root / "comfyui" / f"frame-{index:06d}"
        source_frame_relative = source_frame.resolve().relative_to(media_root).as_posix()

        def frame_progress(percent: int, stage: str, message: str) -> None:
            scaled = 10 + int(((index - 1 + percent / 100) / frame_count) * 78)
            progress(
                min(88, scaled),
                f"frame_{index}_{stage}",
                f"Frame {index}/{frame_count}: {message}",
            )

        try:
            result = client.run_workflow(
                "realesrgan_upscale_v1",
                {"source_path": source_frame_relative, "scale": payload["scale"]},
                frame_output_dir,
                progress=frame_progress,
                cancel_check=cancel_check,
            )
        except ComfyUICancelled as exc:
            raise MediaJobCancelled("Video enhancement was cancelled during frame inference.") from exc
        selected = None
        for output in result.get("outputs", []):
            output_path = frame_output_dir / output["saved_as"]
            if output_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            metadata = probe_media(output_path)
            image_stream = next(
                (
                    stream
                    for stream in metadata.get("streams", [])
                    if stream.get("codec_type") == "video"
                ),
                None,
            )
            if image_stream is None:
                continue
            dimensions = (int(image_stream.get("width", 0)), int(image_stream.get("height", 0)))
            if dimensions != (expected_width, expected_height):
                raise ComfyUIError(
                    f"Real-ESRGAN frame {index} returned {dimensions[0]}x{dimensions[1]}, "
                    f"expected {expected_width}x{expected_height}."
                )
            selected = output_path
            break
        if selected is None:
            raise ComfyUIError(f"Real-ESRGAN did not return a valid image for frame {index}.")
        output_bytes += selected.stat().st_size
        if output_bytes > settings.max_model_output_bytes:
            raise ComfyUIError("Enhanced frame sequence exceeded the configured output size limit.")
        enhanced_frame = enhanced_dir / f"frame-{index:06d}.png"
        shutil.copyfile(selected, enhanced_frame)
        enhanced_frames.append(enhanced_frame)
        if index == frame_count or index % 10 == 0:
            progress(
                min(88, 10 + int(index / frame_count * 78)),
                "frame_enhanced",
                f"Enhanced {index}/{frame_count} frames.",
            )

    if cancel_check():
        raise MediaJobCancelled("Video enhancement was cancelled before MP4 assembly.")
    destination = output_root / f"{job_id}-enhanced.mp4"
    progress(
        90,
        "assembling_enhanced_video",
        "Reassembling enhanced frames and restoring source audio.",
    )
    render = assemble_upscaled_video(
        source=source,
        frames_directory=enhanced_dir,
        destination=destination,
        frame_count=frame_count,
        fps=extraction["fps"],
        duration_seconds=extraction["source_duration_seconds"],
        expected_width=expected_width,
        expected_height=expected_height,
        crf=payload["crf"],
        cancel_check=cancel_check,
    )
    output_asset = register_generated_video_asset(
        project_id,
        job_id,
        destination,
        max_bytes=settings.max_model_output_bytes,
    )
    manifest = {
        "schema_version": 1,
        "project_id": project_id,
        "job_id": job_id,
        "source_asset_id": str(job["asset_id"]),
        "source_duration_seconds": extraction["source_duration_seconds"],
        "source_width": extraction["source_width"],
        "source_height": extraction["source_height"],
        "output_width": expected_width,
        "output_height": expected_height,
        "fps": extraction["fps"],
        "scale": payload["scale"],
        "frame_count": frame_count,
        "audio_preserved": render["has_audio"],
        "output_asset_id": output_asset["id"],
        "render": render,
    }
    manifest_path = output_root / "enhancement.json"
    atomic_write_json(manifest_path, manifest)
    progress(97, "enhancement_complete", "Enhanced MP4 was verified and registered in the project.")
    return {
        "output_asset_id": output_asset["id"],
        "output_asset": {
            "id": output_asset["id"],
            "asset_type": output_asset["asset_type"],
            "relative_path": output_asset["relative_path"],
        },
        "relative_path": output_asset["relative_path"],
        "download_url": (
            f"/v1/projects/{project_id}/jobs/{job_id}/files/"
            f"{Path(output_asset['relative_path']).name}"
        ),
        "manifest_path": manifest_path.relative_to(project_dir(project_id)).as_posix(),
        "source_duration_seconds": extraction["source_duration_seconds"],
        "output_duration_seconds": render["duration_seconds"],
        "source_width": extraction["source_width"],
        "source_height": extraction["source_height"],
        "output_width": expected_width,
        "output_height": expected_height,
        "fps": extraction["fps"],
        "scale": payload["scale"],
        "frame_count": frame_count,
        "audio_preserved": render["has_audio"],
    }


def execute_video_sequence(
    job: dict[str, Any],
    source: Path,
    *,
    progress,
    cancel_check,
) -> dict:
    """Generate approved shots serially, register them, and assemble one MP4."""
    project_id = str(UUID(job["project_id"]))
    job_id = str(UUID(job["id"]))
    payload = job["payload"]
    shots = payload["shots"]
    if not 2 <= len(shots) <= 8:
        raise ValueError("A generated sequence requires between two and eight shots.")

    media_root = Path(settings.media_root).expanduser().resolve()
    try:
        reference_path = source.resolve().relative_to(media_root).as_posix()
    except ValueError as exc:
        raise ValueError("Generation reference must remain inside MEDIA_ROOT.") from exc

    output_root = project_dir(project_id) / "ai_outputs" / str(job_id)
    output_root.mkdir(parents=True, exist_ok=True)
    client = ComfyUIClient()
    shot_assets: list[dict] = []
    total_duration = sum(int(shot["duration_seconds"]) for shot in shots)
    if total_duration > 80:
        raise ValueError("A generated sequence cannot exceed 80 seconds.")

    def report_shot_progress(index: int, percentage: int, stage: str, message: str) -> None:
        scaled = 10 + int(((index + percentage / 100) / len(shots)) * 68)
        progress(
            min(78, scaled),
            f"shot_{index + 1}_{stage}",
            f"Shot {index + 1}/{len(shots)}: {message}",
        )

    for index, shot in enumerate(shots):
        if cancel_check():
            raise RuntimeError("Multi-shot generation was cancelled.")
        shot_dir = output_root / f"shot-{index + 1:02d}"
        shot_dir.mkdir(parents=True, exist_ok=True)

        def shot_progress(percent: int, stage: str, message: str) -> None:
            report_shot_progress(index, percent, stage, message)

        generated = client.run_workflow(
            "wan22_ti2v_5b_v1",
            {
                "source_path": reference_path,
                "prompt": shot["prompt"],
                "negative_prompt": shot["negative_prompt"],
                "duration_seconds": shot["duration_seconds"],
                "width": payload["generation_width"],
                "height": payload["generation_height"],
            },
            shot_dir,
            progress=shot_progress,
            cancel_check=cancel_check,
        )
        video_output = None
        for output in generated.get("outputs", []):
            output_path = shot_dir / output["saved_as"]
            metadata = probe_media(output_path)
            if any(
                stream.get("codec_type") == "video"
                for stream in metadata.get("streams", [])
            ):
                video_output = (output_path, metadata)
                break
        if video_output is None:
            raise ComfyUIError(
                f"Wan 2.2 shot {index + 1} did not return a valid video stream."
            )

        shot_path, shot_metadata = video_output
        if int(payload["interpolate_factor"]) == 2:
            if cancel_check():
                raise RuntimeError("Multi-shot interpolation was cancelled.")
            interpolation_dir = shot_dir / "interpolated"
            interpolation = client.run_workflow(
                "rife_interpolate_v1",
                {
                    "source_path": shot_path.resolve().relative_to(media_root).as_posix(),
                    "factor": 2,
                },
                interpolation_dir,
                progress=shot_progress,
                cancel_check=cancel_check,
            )
            interpolated = None
            for output in interpolation.get("outputs", []):
                candidate = interpolation_dir / output["saved_as"]
                metadata = probe_media(candidate)
                if any(
                    stream.get("codec_type") == "video"
                    for stream in metadata.get("streams", [])
                ):
                    interpolated = (candidate, metadata)
                    break
            if interpolated is None:
                raise ComfyUIError(
                    f"RIFE did not return a valid video for shot {index + 1}."
                )
            shot_path, shot_metadata = interpolated

        duration_value = shot_metadata.get("format", {}).get("duration")
        try:
            actual_duration = float(duration_value)
        except (TypeError, ValueError) as exc:
            raise ComfyUIError(
                f"Generated shot {index + 1} has no valid duration."
            ) from exc
        requested_duration = float(shot["duration_seconds"])
        if actual_duration + 0.05 < requested_duration:
            raise ComfyUIError(
                f"Generated shot {index + 1} is shorter than its approved duration."
            )

        shot_job_id = str(
            uuid5(NAMESPACE_URL, f"ai-video-studio:{job_id}:shot:{index + 1}")
        )
        asset = register_generated_video_asset(
            project_id,
            shot_job_id,
            shot_path,
            max_bytes=settings.max_model_output_bytes,
        )
        shot_assets.append(
            {
                "index": index + 1,
                "asset_id": asset["id"],
                "relative_path": asset["relative_path"],
                "duration_seconds": requested_duration,
                "generated_duration_seconds": round(actual_duration, 6),
                "prompt": shot["prompt"],
            }
        )
        progress(
            min(80, 10 + int(((index + 1) / len(shots)) * 70)),
            "shot_registered",
            f"Shot {index + 1}/{len(shots)} is validated and saved to the project.",
        )

    if cancel_check():
        raise RuntimeError("Multi-shot assembly was cancelled.")

    sources: dict[str, Path] = {}
    timeline_clips: list[dict] = []
    cursor = 0.0
    for shot in shot_assets:
        asset_id = str(UUID(shot["asset_id"]))
        asset_path = asset_dir(project_id, asset_id)
        manifest_path = asset_path / "asset.json"
        asset = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_path = (asset_path / asset["stored_filename"]).resolve()
        if not source_path.is_relative_to(asset_path.resolve()) or not source_path.is_file():
            raise FileNotFoundError("Generated shot asset is missing from its project directory.")
        sources[asset_id] = source_path
        clip_duration = float(shot["duration_seconds"])
        timeline_clips.append(
            {
                "clip_id": str(uuid5(NAMESPACE_URL, f"{job_id}:clip:{shot['index']}")),
                "asset_id": asset_id,
                "source_start_seconds": 0.0,
                "duration_seconds": clip_duration,
                "timeline_start_seconds": cursor,
            }
        )
        cursor += clip_duration

    timeline_manifest = {
        "schema_version": 2,
        "timeline_id": str(job_id),
        "project_id": project_id,
        "fps": payload["fps"],
        "duration_seconds": round(cursor, 6),
        "clips": timeline_clips,
        "status": "draft",
    }
    destination = project_dir(project_id) / "exports" / f"{job_id}-multi-shot.mp4"
    progress(83, "assembling_shots", "Assembling the validated generated shots in timeline order.")
    rendered = render_multi_source_timeline(
        sources=sources,
        timeline_manifest=timeline_manifest,
        destination=destination,
        width=payload["output_width"],
        height=payload["output_height"],
        fps=payload["fps"],
        crf=payload["crf"],
        cancel_check=cancel_check,
    )
    output_asset = register_generated_video_asset(
        project_id,
        job_id,
        destination,
        max_bytes=settings.max_model_output_bytes,
    )
    sequence_manifest = {
        "schema_version": 1,
        "project_id": project_id,
        "job_id": str(job_id),
        "reference_asset_id": str(job["asset_id"]),
        "shot_count": len(shot_assets),
        "duration_seconds": round(cursor, 6),
        "fps": payload["fps"],
        "interpolate_factor": payload["interpolate_factor"],
        "shots": shot_assets,
        "output_asset_id": output_asset["id"],
        "render": rendered,
    }
    atomic_write_json(output_root / "sequence.json", sequence_manifest)
    progress(
        96,
        "sequence_complete",
        "The multi-shot video is assembled and registered as a project asset.",
    )
    return {
        "shot_count": len(shot_assets),
        "duration_seconds": round(cursor, 6),
        "interpolate_factor": payload["interpolate_factor"],
        "shots": shot_assets,
        "sequence_manifest_path": (
            output_root / "sequence.json"
        ).relative_to(project_dir(project_id)).as_posix(),
        "output_asset_id": output_asset["id"],
        "output_asset": {
            "id": output_asset["id"],
            "asset_type": output_asset["asset_type"],
            "relative_path": output_asset["relative_path"],
        },
        "relative_path": output_asset["relative_path"],
        "download_url": (
            f"/v1/projects/{project_id}/jobs/{job_id}/files/"
            f"{Path(output_asset['relative_path']).name}"
        ),
        "render": rendered,
    }


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
        f"/v1/projects/{job['project_id']}/jobs/"
        f"{job['id']}/files/{quote(destination.name, safe='')}"
    )
    return result
