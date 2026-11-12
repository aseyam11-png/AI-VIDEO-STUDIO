import hashlib
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
from app.media.quality import analyze_video_quality, persist_quality_report
from app.media.speech import transcribe_local
from app.media.storage import (
    asset_dir,
    atomic_write_json,
    get_asset,
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


def _verify_product_identity_reference(
    project_id: str,
    asset_id: str,
    source: Path,
    expected_sha256: str,
) -> dict:
    """Verify that the image reference is an immutable, project-scoped asset."""
    asset = get_asset(project_id, asset_id)
    if asset.get("asset_type") != "image":
        raise ValueError("Product identity lock requires an image reference asset.")
    asset_directory = asset_dir(project_id, asset_id).resolve()
    expected_source = (asset_directory / str(asset.get("stored_filename", ""))).resolve()
    if (
        not expected_source.is_relative_to(asset_directory)
        or not expected_source.is_file()
        or source.resolve() != expected_source
    ):
        raise ValueError(
            "Product identity reference path does not match the selected project asset."
        )
    stored_sha256 = asset.get("sha256")
    if stored_sha256 != expected_sha256:
        raise ValueError("Product identity reference fingerprint changed after workflow approval.")
    digest = hashlib.sha256()
    with expected_source.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise ValueError("Product identity reference file failed SHA-256 integrity verification.")
    return asset


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
    if job["job_type"] == "video.generate" and job["payload"].get("product_identity_lock"):
        _verify_product_identity_reference(
            job["project_id"],
            job["asset_id"],
            source,
            job["payload"]["identity_reference_sha256"],
        )
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
    generated_video_path: Path | None = None
    safe_output_dir = output_dir.resolve()
    for output_index, output in enumerate(result["outputs"], start=1):
        output_path = (output_dir / output["saved_as"]).resolve()
        if not output_path.is_relative_to(safe_output_dir):
            raise ComfyUIError("ComfyUI returned an output path outside the job directory.")
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
        is_video_file = (
            output_path.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}
            and any(stream.get("codec_type") == "video" for stream in streams)
        )
        if (
            output_path.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv", ".gif"}
            and any(stream.get("codec_type") == "video" for stream in streams)
        ):
            video_stream_found = True
            if (
                job["job_type"] in {"video.generate", "video.interpolate"}
                and is_video_file
            ):
                generated_video_path = output_path
            elif is_video_file:
                report_key = f"job-{job['id']}-{output_index}"
                quality_report = analyze_video_quality(
                    output_path,
                    cancel_check=cancel_check,
                )
                persist_quality_report(
                    job["project_id"], report_key, quality_report
                )
                if not quality_report["can_export"]:
                    output_path.unlink(missing_ok=True)
                    raise ComfyUIError(
                        "ComfyUI video output failed the quality gate: "
                        f"{quality_report.get('summary', {}).get('error_count', 0)} "
                        "blocking issue(s)."
                    )
                output["quality_report"] = quality_report
                output["quality_report_key"] = report_key
        output["download_url"] = (
            f"/v1/projects/{job['project_id']}/jobs/{job['id']}/files/"
            f"{quote(output['saved_as'], safe='')}"
        )
    if job["job_type"] in {"video.generate", "video.interpolate"} and not video_stream_found:
        raise ComfyUIError(f"ComfyUI workflow {workflow_id} did not produce a video file.")
    if job["job_type"] in {"video.generate", "video.interpolate"}:
        if generated_video_path is None:
            raise ComfyUIError(
                "Video generation/interpolation must produce a supported MP4/MOV/MKV/WEBM asset."
            )
        if job["job_type"] == "video.generate":
            requested_duration = job["payload"].get("duration_seconds")
            expected_duration = (
                float(requested_duration) if requested_duration is not None else None
            )
        else:
            source_metadata = probe_media(source)
            source_duration = source_metadata.get("format", {}).get("duration")
            expected_duration = (
                float(source_duration) if source_duration is not None else None
            )
        quality_report = analyze_video_quality(
            generated_video_path,
            expected_duration_seconds=expected_duration,
            cancel_check=cancel_check,
        )
        if not quality_report["can_export"]:
            persist_quality_report(
                job["project_id"], f"job-{job['id']}", quality_report
            )
            generated_video_path.unlink(missing_ok=True)
            raise ComfyUIError(
                "Video quality gate blocked this model output: "
                f"{quality_report.get('summary', {}).get('error_count', 0)} "
                "blocking issue(s)."
            )
        output_asset = register_generated_video_asset(
            job["project_id"],
            job["id"],
            generated_video_path,
            max_bytes=settings.max_model_output_bytes,
        )
        persist_quality_report(
            job["project_id"], f"asset-{output_asset['id']}", quality_report
        )
        result["output_asset_id"] = output_asset["id"]
        result["output_asset"] = {
            "id": output_asset["id"],
            "asset_type": output_asset["asset_type"],
            "relative_path": output_asset["relative_path"],
        }
        result["quality_report"] = quality_report
        result["download_url"] = (
            f"/v1/projects/{job['project_id']}/jobs/{job['id']}/files/"
            f"{generated_video_path.name}"
        )
        if job["payload"].get("product_identity_lock"):
            result["product_identity_lock"] = True
            result["identity_reference_sha256"] = job["payload"]["identity_reference_sha256"]
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
    if not source.resolve().is_relative_to(media_root):
        raise ValueError("Video enhancement source must remain inside MEDIA_ROOT.")

    output_root = project_dir(project_id) / "ai_outputs" / str(job_id)
    # Clear incomplete artifacts from a previous attempt before reusing this idempotent job path.
    shutil.rmtree(output_root, ignore_errors=True)
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
        max_total_bytes=settings.max_model_output_bytes,
        cancel_check=cancel_check,
    )
    expected_width = extraction["source_width"] * payload["scale"]
    expected_height = extraction["source_height"] * payload["scale"]
    if expected_width > 7680 or expected_height > 4320:
        raise ValueError("Upscaled output would exceed the supported 8K dimensions.")

    client = ComfyUIClient()
    output_bytes = 0
    frame_count = extraction["frame_count"]
    for index in range(1, frame_count + 1):
        if cancel_check():
            raise MediaJobCancelled("Video enhancement was cancelled between frame jobs.")
        source_frame = extracted_dir / f"frame-{index:06d}.png"
        frame_output_dir = output_root / "comfyui" / f"frame-{index:06d}"
        source_frame_relative = source_frame.resolve().relative_to(media_root).as_posix()

        def frame_progress(
            percent: int,
            stage: str,
            message: str,
            frame_index: int = index,
        ) -> None:
            scaled = 10 + int(((frame_index - 1 + percent / 100) / frame_count) * 78)
            progress(
                min(88, scaled),
                f"frame_{frame_index}_{stage}",
                f"Frame {frame_index}/{frame_count}: {message}",
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
            raise MediaJobCancelled(
                "Video enhancement was cancelled during frame inference."
            ) from exc
        selected = None
        for output in result.get("outputs", []):
            output_path = (frame_output_dir / output["saved_as"]).resolve()
            if not output_path.is_relative_to(frame_output_dir.resolve()):
                raise ComfyUIError(
                    "Real-ESRGAN returned an output path outside its frame directory."
                )
            if output_path.suffix.lower() != ".png":
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
    source_has_audio = any(
        stream.get("codec_type") == "audio"
        for stream in probe_media(source).get("streams", [])
    )
    quality_report = analyze_video_quality(
        destination,
        expected_duration_seconds=extraction["source_duration_seconds"],
        expected_width=expected_width,
        expected_height=expected_height,
        expected_fps=float(extraction["fps"]),
        expected_audio=source_has_audio,
        cancel_check=cancel_check,
    )
    if not quality_report["can_export"]:
        persist_quality_report(project_id, f"job-{job_id}", quality_report)
        destination.unlink(missing_ok=True)
        raise ComfyUIError(
            "Real-ESRGAN output failed the video quality gate: "
            f"{quality_report.get('summary', {}).get('error_count', 0)} "
            "blocking issue(s)."
        )
    output_asset = register_generated_video_asset(
        project_id,
        job_id,
        destination,
        max_bytes=settings.max_model_output_bytes,
    )
    persist_quality_report(
        project_id, f"asset-{output_asset['id']}", quality_report
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
        "quality_report": quality_report,
        "render": render,
    }
    manifest_path = output_root / "enhancement.json"
    atomic_write_json(manifest_path, manifest)
    shutil.rmtree(output_root / "frames", ignore_errors=True)
    shutil.rmtree(output_root / "comfyui", ignore_errors=True)
    destination.unlink(missing_ok=True)
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
        "quality_report": quality_report,
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

    identity_reference = None
    if payload.get("product_identity_lock"):
        identity_reference = _verify_product_identity_reference(
            project_id,
            str(job["asset_id"]),
            source,
            payload["identity_reference_sha256"],
        )

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

        def shot_progress(
            percent: int, stage: str, message: str, shot_index: int = index
        ) -> None:
            report_shot_progress(shot_index, percent, stage, message)

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
                "identity_reference_sha256": (
                    payload.get("identity_reference_sha256")
                    if payload.get("product_identity_lock")
                    else None
                ),
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
    quality_report = analyze_video_quality(
        destination,
        expected_duration_seconds=cursor,
        expected_width=payload["output_width"],
        expected_height=payload["output_height"],
        expected_fps=float(payload["fps"]),
        cancel_check=cancel_check,
    )
    if not quality_report["can_export"]:
        persist_quality_report(project_id, f"job-{job_id}", quality_report)
        destination.unlink(missing_ok=True)
        raise ComfyUIError(
            "Wan sequence output failed the video quality gate: "
            f"{quality_report.get('summary', {}).get('error_count', 0)} "
            "blocking issue(s)."
        )
    output_asset = register_generated_video_asset(
        project_id,
        job_id,
        destination,
        max_bytes=settings.max_model_output_bytes,
    )
    persist_quality_report(
        project_id, f"asset-{output_asset['id']}", quality_report
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
        "product_identity_lock": bool(payload.get("product_identity_lock")),
        "identity_reference": (
            {
                "asset_id": str(job["asset_id"]),
                "sha256": payload["identity_reference_sha256"],
                "integrity_verified": True,
                "human_review_required": True,
                "review_status": "pending",
            }
            if identity_reference
            else None
        ),
        "shots": shot_assets,
        "output_asset_id": output_asset["id"],
        "quality_report": quality_report,
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
        "quality_report": quality_report,
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
    expected_duration = (
        (int(payload["frame_end"]) - int(payload["frame_start"]) + 1)
        / float(payload["fps"])
    )
    quality_report = analyze_video_quality(
        destination,
        expected_duration_seconds=expected_duration,
        expected_width=payload["width"],
        expected_height=payload["height"],
        expected_fps=float(payload["fps"]),
        cancel_check=cancel_check,
    )
    if not quality_report["can_export"]:
        persist_quality_report(
            job["project_id"], f"job-{job['id']}", quality_report
        )
        destination.unlink(missing_ok=True)
        raise ComfyUIError(
            "Blender motion output failed the video quality gate: "
            f"{quality_report.get('summary', {}).get('error_count', 0)} "
            "blocking issue(s)."
        )
    report_key = f"job-{UUID(job['id'])}"
    persist_quality_report(job["project_id"], report_key, quality_report)
    result["quality_report"] = quality_report
    result["quality_report_key"] = report_key
    result["relative_path"] = destination.relative_to(project_dir(job["project_id"])).as_posix()
    result["download_url"] = (
        f"/v1/projects/{job['project_id']}/jobs/"
        f"{job['id']}/files/{quote(destination.name, safe='')}"
    )
    return result
