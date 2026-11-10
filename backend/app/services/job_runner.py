import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.db.models import JobRecord, OutboxRecord
from app.db.session import session_scope
from app.integrations.ffmpeg import MediaToolError, probe_media
from app.media.pipeline import (
    MediaJobCancelled,
    build_timeline,
    create_proxy,
    detect_scenes,
    render_multi_source_timeline,
    render_timeline,
)
from app.media.storage import (
    asset_dir,
    atomic_write_json,
    get_asset,
    project_dir,
    read_json,
    register_generated_video_asset,
    update_asset_manifest,
)
from app.schemas.jobs import JobType
from app.services.jobs import append_event, job_to_dict
from app.services.model_jobs import (
    execute_audio_enhancement,
    execute_blender_job,
    execute_comfyui_job,
    execute_speech_transcription,
)
from sqlalchemy import select


class JobCancelled(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_source_path(project_id: str, asset_id: str, stored_filename: str) -> Path:
    directory = asset_dir(project_id, asset_id).resolve()
    source = (directory / stored_filename).resolve()
    if not source.is_relative_to(directory) or not source.is_file():
        raise FileNotFoundError("The source media file is missing or outside its asset directory.")
    return source


def _is_cancelled(job_id: str, dispatch_generation: int) -> bool:
    with session_scope() as db:
        job = db.get(JobRecord, job_id)
        return (
            job is None
            or job.dispatch_generation != dispatch_generation
            or job.status != "running"
            or job.cancel_requested
        )


def _set_progress(
    job_id: str,
    dispatch_generation: int,
    progress: int,
    stage: str,
    message: str,
) -> None:
    with session_scope() as db:
        job = db.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
        if job is None or job.dispatch_generation != dispatch_generation:
            raise JobCancelled("Worker attempt has been superseded.")
        if job.status == "cancel_requested" or job.cancel_requested:
            raise JobCancelled("Cancellation requested.")
        if job.status != "running":
            raise JobCancelled("Job is no longer running.")
        job.progress = max(job.progress, min(99, max(0, progress)))
        job.progress_stage = stage[:80]
        job.progress_message = message[:500]
        job.heartbeat_at = _now()
        append_event(
            db,
            job,
            "progress",
            message,
            details={"stage": stage, "progress_semantics": "stage_checkpoint"},
        )


def _heartbeat_loop(
    job_id: str,
    dispatch_generation: int,
    stop_event: threading.Event,
    cancel_event: threading.Event,
) -> None:
    interval = max(3, settings.job_heartbeat_interval_seconds)
    while not stop_event.wait(interval):
        try:
            with session_scope() as db:
                job = db.get(JobRecord, job_id)
                if job is None or job.dispatch_generation != dispatch_generation:
                    cancel_event.set()
                    return
                if job.status == "cancel_requested" or job.cancel_requested:
                    cancel_event.set()
                    return
                if job.status != "running":
                    cancel_event.set()
                    return
                job.heartbeat_at = _now()
        except Exception:
            # The task will report its own failure; heartbeat errors are not swallowed
            # into a success state.
            cancel_event.set()
            return


def _execute_media_job(
    job: dict[str, Any],
    *,
    progress: Callable[[int, str, str], None],
    cancel_check: Callable[[], bool],
) -> dict[str, Any]:
    project_id = job["project_id"]
    asset_id = job["asset_id"]
    if not asset_id:
        raise ValueError("A media job requires an asset_id.")
    asset = get_asset(project_id, asset_id)
    asset_type = asset.get("asset_type")
    source = _safe_source_path(project_id, asset_id, asset["stored_filename"])
    payload = job["payload"]
    job_type = job["job_type"]

    if cancel_check():
        raise JobCancelled("Cancellation requested before execution.")

    if job_type == JobType.SPEECH_TRANSCRIBE.value:
        if asset_type != "video":
            raise ValueError("Speech transcription requires a video asset with audio.")
        progress(10, "loading_whisperx", "Preparing the selected WhisperX profile.")
        result = execute_speech_transcription(
            project_id, asset_id, job["id"], source, payload, cancel_check=cancel_check
        )
        progress(95, "transcription_saved", "WhisperX transcription was saved.")
        return result

    if job_type == JobType.AUDIO_ENHANCE.value:
        if asset_type != "video":
            raise ValueError("Audio enhancement requires a video asset with audio.")
        progress(10, "extracting_audio", "Extracting the source audio for DeepFilterNet.")
        result = execute_audio_enhancement(
            project_id, asset_id, job["id"], source, payload, cancel_check=cancel_check
        )
        progress(95, "audio_muxed", "Enhanced audio was muxed into a new video file.")
        generated_path = project_dir(project_id) / result["relative_path"]
        output_asset = register_generated_video_asset(
            project_id,
            job["id"],
            generated_path,
            max_bytes=settings.max_model_output_bytes,
        )
        result["output_asset_id"] = output_asset["id"]
        result["output_asset"] = {
            "id": output_asset["id"],
            "asset_type": output_asset["asset_type"],
            "relative_path": output_asset["relative_path"],
        }
        return result

    if job_type in {
        JobType.VISION_DETECT.value,
        JobType.VISION_TRACK.value,
        JobType.VIDEO_GENERATE.value,
        JobType.VIDEO_UPSCALE.value,
        JobType.VIDEO_INTERPOLATE.value,
    }:
        if job_type in {
            JobType.VISION_TRACK.value,
            JobType.VIDEO_INTERPOLATE.value,
        } and asset_type != "video":
            raise ValueError("This model workflow requires a video asset.")
        if asset_type not in {"video", "image"}:
            raise ValueError("The selected model workflow requires an image or video asset.")
        progress(10, "preparing_model_workflow", "Preparing the approved ComfyUI workflow.")
        result = execute_comfyui_job(
            job, source, progress=progress, cancel_check=cancel_check
        )
        if job_type in {
            JobType.VIDEO_GENERATE.value,
            JobType.VIDEO_INTERPOLATE.value,
            JobType.VIDEO_UPSCALE.value,
        }:
            video_output = next(
                (
                    output
                    for output in result.get("outputs", [])
                    if any(
                        stream.get("codec_type") == "video"
                        for stream in (output.get("metadata", {}).get("streams", []))
                    )
                ),
                None,
            )
            if (
                job_type == JobType.VIDEO_UPSCALE.value
                and asset_type == "video"
                and video_output is None
            ):
                raise ValueError(
                    "The configured Real-ESRGAN workflow did not return a video. "
                    "Video output is required before a downstream timeline/export step."
                )
            if video_output is not None:
                generated_path = (
                    project_dir(project_id)
                    / result["files_directory"]
                    / video_output["saved_as"]
                )
                output_asset = register_generated_video_asset(
                    project_id,
                    job["id"],
                    generated_path,
                    max_bytes=settings.max_model_output_bytes,
                )
                result["output_asset_id"] = output_asset["id"]
                result["output_asset"] = {
                    "id": output_asset["id"],
                    "asset_type": output_asset["asset_type"],
                    "relative_path": output_asset["relative_path"],
                }
        return result

    if job_type == JobType.MOTION_BLENDER.value:
        progress(10, "preparing_blender", "Validating the approved Blender project template.")
        result = execute_blender_job(job, cancel_check=cancel_check)
        generated_path = project_dir(project_id) / result["relative_path"]
        output_asset = register_generated_video_asset(
            project_id,
            job["id"],
            generated_path,
            max_bytes=settings.max_model_output_bytes,
        )
        result["output_asset_id"] = output_asset["id"]
        result["output_asset"] = {
            "id": output_asset["id"],
            "asset_type": output_asset["asset_type"],
            "relative_path": output_asset["relative_path"],
        }
        progress(95, "blender_rendered", "Blender completed the motion graphics render.")
        return result

    if job_type == JobType.VIDEO_ASSEMBLE.value:
        progress(10, "validating_generated_shots", "Validating generated scene assets before assembly.")
        generated_asset_ids = payload.get("generated_asset_ids", [])
        planned_durations = payload.get("scene_durations_seconds", [])
        if (
            not isinstance(generated_asset_ids, list)
            or not 2 <= len(generated_asset_ids) <= 8
            or len(generated_asset_ids) != len(planned_durations)
            or len(set(generated_asset_ids)) != len(generated_asset_ids)
        ):
            raise ValueError("Video assembly requires two to eight unique generated video assets.")

        sources: dict[str, Path] = {}
        clips: list[dict] = []
        actual_durations: list[float] = []
        adjustments: list[dict] = []
        for index, generated_asset_id in enumerate(generated_asset_ids, start=1):
            if cancel_check():
                raise JobCancelled("Cancellation requested while preparing generated shots.")
            generated_asset = get_asset(project_id, generated_asset_id)
            if generated_asset.get("asset_type") != "video":
                raise ValueError("Every generated shot must be a video asset from this project.")
            generated_source = _safe_source_path(
                project_id,
                generated_asset_id,
                generated_asset["stored_filename"],
            )
            metadata = probe_media(generated_source)
            if not any(
                stream.get("codec_type") == "video"
                for stream in metadata.get("streams", [])
            ):
                raise MediaToolError(
                    f"Generated shot {index} does not contain a valid video stream."
                )
            try:
                source_duration = float(metadata.get("format", {}).get("duration"))
                planned_duration = float(planned_durations[index - 1])
            except (TypeError, ValueError) as exc:
                raise MediaToolError(
                    f"Generated shot {index} has no valid duration metadata."
                ) from exc
            if not 0.1 < source_duration <= 60:
                raise MediaToolError(f"Generated shot {index} duration is outside safe limits.")
            clip_duration = min(source_duration, planned_duration)
            if clip_duration <= 0:
                raise MediaToolError(f"Generated shot {index} has an invalid render duration.")
            asset_uuid = str(UUID(generated_asset_id))
            clip_id = str(uuid4())
            sources[asset_uuid] = generated_source
            clips.append(
                {
                    "clip_id": clip_id,
                    "asset_id": asset_uuid,
                    "source_start_seconds": 0.0,
                    "duration_seconds": round(clip_duration, 6),
                }
            )
            actual_durations.append(round(clip_duration, 6))
            if abs(clip_duration - planned_duration) > 0.05:
                adjustments.append(
                    {
                        "scene_index": index,
                        "planned_duration_seconds": planned_duration,
                        "rendered_duration_seconds": round(clip_duration, 6),
                        "reason": "Generated video duration differs from the requested scene duration.",
                    }
                )

        timeline_manifest = {
            "schema_version": 2,
            "project_id": project_id,
            "fps": payload["fps"],
            "duration_seconds": round(sum(actual_durations), 6),
            "clips": clips,
        }
        output_dir = project_dir(project_id) / "ai_outputs" / str(UUID(job["id"]))
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / "assembled.mp4"
        progress(30, "assembling_generated_shots", "Assembling generated scenes in approved storyboard order.")
        render_result = render_multi_source_timeline(
            sources=sources,
            timeline_manifest=timeline_manifest,
            destination=destination,
            width=payload["width"],
            height=payload["height"],
            fps=payload["fps"],
            crf=payload["crf"],
            cancel_check=cancel_check,
        )
        if cancel_check():
            raise JobCancelled("Cancellation requested after generated-shot assembly.")
        output_asset = register_generated_video_asset(
            project_id,
            job["id"],
            destination,
            max_bytes=settings.max_model_output_bytes,
        )
        progress(95, "registering_assembled_video", "Multi-shot MP4 was rendered and registered as a project asset.")
        return {
            **render_result,
            "scene_count": len(clips),
            "scene_durations_seconds": actual_durations,
            "duration_seconds": round(sum(actual_durations), 6),
            "duration_adjustments": adjustments,
            "output_asset_id": output_asset["id"],
            "output_asset": {
                "id": output_asset["id"],
                "asset_type": output_asset["asset_type"],
                "relative_path": output_asset["relative_path"],
            },
            "download_url": (
                f"/v1/projects/{project_id}/assets/{output_asset['id']}/file"
            ),
        }

    if asset_type != "video":
        raise ValueError("Deterministic media processing jobs require a video asset.")

    if job_type == JobType.MEDIA_PROXY.value:
        progress(10, "probing", "Checking source streams and proxy settings.")
        result = create_proxy(
            source,
            asset_dir(project_id, asset_id) / "proxy.mp4",
            max_width=payload["max_width"],
            fps=payload["fps"],
            cancel_check=cancel_check,
        )
        update_asset_manifest(project_id, asset_id, {"proxy": result})
        progress(95, "finalizing", "Proxy created and verified.")
        return result

    if job_type == JobType.MEDIA_SCENES.value:
        progress(10, "analyzing", "Analyzing video frames for scene boundaries.")
        scenes = detect_scenes(
            source,
            threshold=payload["threshold"],
            min_scene_len=payload["min_scene_len"],
            cancel_check=cancel_check,
        )
        manifest = {
            "asset_id": asset_id,
            "project_id": project_id,
            "threshold": payload["threshold"],
            "min_scene_len": payload["min_scene_len"],
            "scene_count": len(scenes),
            "scenes": scenes,
        }
        atomic_write_json(asset_dir(project_id, asset_id) / "scenes.json", manifest)
        progress(95, "finalizing", f"Detected {len(scenes)} candidate scene(s).")
        return manifest

    if job_type == JobType.MEDIA_TIMELINE.value:
        progress(10, "preparing_timeline", "Preparing scene ranges for the timeline.")
        if cancel_check():
            raise JobCancelled("Cancellation requested.")
        scenes_path = asset_dir(project_id, asset_id) / "scenes.json"
        if scenes_path.is_file():
            scenes = read_json(scenes_path)["scenes"]
        else:
            scenes = detect_scenes(source, cancel_check=cancel_check)
        manifest, timeline_path = build_timeline(
            project_id=project_id,
            asset_id=asset_id,
            source=source,
            scenes=scenes,
            fps=payload["fps"],
            cancel_check=cancel_check,
        )
        relative_path = timeline_path.relative_to(project_dir(project_id)).as_posix()
        progress(95, "finalizing", "OpenTimelineIO and JSON timeline manifests created.")
        return {**manifest, "timeline_path": relative_path}

    if job_type == JobType.MEDIA_RENDER.value:
        progress(10, "preparing_render", "Validating the saved timeline and render settings.")
        timeline_path = project_dir(project_id) / "timelines" / f"{asset_id}.json"
        if not timeline_path.is_file():
            raise FileNotFoundError("Create the timeline before submitting a render job.")
        timeline = read_json(timeline_path)
        if timeline.get("project_id") != project_id or timeline.get("asset_id") != asset_id:
            raise ValueError("Timeline project or asset identity does not match the job.")
        destination = project_dir(project_id) / "exports" / f"{asset_id}-preview.mp4"
        result = render_timeline(
            source=source,
            timeline_manifest=timeline,
            destination=destination,
            width=payload["width"],
            height=payload["height"],
            fps=payload["fps"],
            crf=payload["crf"],
            cancel_check=cancel_check,
        )
        progress(95, "finalizing", "Rendered MP4 was created and probed.")
        return {
            **result,
            "download_url": f"/v1/projects/{project_id}/assets/{asset_id}/rendered",
        }

    raise ValueError("Unsupported media job type.")


def _finish_job(
    job_id: str,
    dispatch_generation: int,
    *,
    result: dict[str, Any] | None = None,
    error: Exception | None = None,
) -> None:
    with session_scope() as db:
        job = db.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
        if (
            job is None
            or job.dispatch_generation != dispatch_generation
            or job.status in {"succeeded", "failed", "cancelled"}
            or job.status not in {"running", "cancel_requested"}
        ):
            return
        if (
            isinstance(error, (JobCancelled, MediaJobCancelled))
            or job.status == "cancel_requested"
            or job.cancel_requested
        ):
            job.status = "cancelled"
            job.cancel_requested = True
            job.progress_stage = "cancelled"
            job.progress_message = "Job cancelled; partial output is not marked as complete."
            job.error_code = "JOB_CANCELLED"
            job.error_message = None
            append_event(db, job, "cancelled", job.progress_message)
        elif error is not None:
            job.status = "failed"
            job.progress_stage = "failed"
            job.progress_message = "Job failed. Review the error details and retry when safe."
            job.error_code = type(error).__name__[:80]
            job.error_message = str(error)[:2000]
            append_event(
                db,
                job,
                "failed",
                job.progress_message,
                details={"error_code": job.error_code, "error_message": job.error_message},
            )
        else:
            job.status = "succeeded"
            job.progress = 100
            job.progress_stage = "completed"
            job.progress_message = "Job completed successfully."
            job.result_json = result or {}
            job.error_code = None
            job.error_message = None
            append_event(db, job, "succeeded", job.progress_message)
        job.finished_at = _now()
        job.heartbeat_at = _now()
        from app.services.workflow_engine import advance_workflow_for_job

        advance_workflow_for_job(db, job)


def run_job_attempt(job_id: str, dispatch_generation: int) -> dict[str, Any]:
    with session_scope() as db:
        job = db.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
        if job is None:
            return {"status": "ignored", "reason": "job_not_found"}
        if job.dispatch_generation != dispatch_generation or job.status != "queued":
            return {"status": "ignored", "reason": "stale_or_duplicate_delivery"}
        if job.cancel_requested:
            job.status = "cancelled"
            job.finished_at = _now()
            job.progress_stage = "cancelled"
            job.progress_message = "Cancelled before the worker claimed the job."
            append_event(db, job, "cancelled", job.progress_message)
            from app.services.workflow_engine import advance_workflow_for_job

            advance_workflow_for_job(db, job)
            return {"status": "cancelled"}
        if job.attempt_count >= job.max_attempts:
            job.status = "failed"
            job.error_code = "ATTEMPT_BUDGET_EXHAUSTED"
            job.error_message = "The configured attempt budget has been exhausted."
            job.finished_at = _now()
            append_event(db, job, "failed", job.error_message)
            from app.services.workflow_engine import advance_workflow_for_job

            advance_workflow_for_job(db, job)
            return {"status": "failed"}
        job.status = "running"
        job.attempt_count += 1
        job.started_at = _now()
        job.heartbeat_at = _now()
        job.progress = max(job.progress, 1)
        job.progress_stage = "starting"
        job.progress_message = "A worker claimed this job."
        job.error_code = None
        job.error_message = None
        append_event(
            db,
            job,
            "started",
            job.progress_message,
            details={"attempt": job.attempt_count},
        )
        from app.services.workflow_engine import mark_workflow_job_running

        mark_workflow_job_running(db, job)
        snapshot = job_to_dict(job)

    stop_event = threading.Event()
    cancel_event = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        args=(job_id, dispatch_generation, stop_event, cancel_event),
        name=f"job-heartbeat-{job_id[:8]}",
        daemon=True,
    )
    heartbeat.start()

    def check_cancel() -> bool:
        return cancel_event.is_set() or _is_cancelled(job_id, dispatch_generation)

    try:
        result = _execute_media_job(
            snapshot,
            progress=lambda value, stage, message: _set_progress(
                job_id, dispatch_generation, value, stage, message
            ),
            cancel_check=check_cancel,
        )
        if check_cancel():
            raise JobCancelled("Cancellation requested.")
        _finish_job(job_id, dispatch_generation, result=result)
    except Exception as exc:
        _finish_job(job_id, dispatch_generation, error=exc)
    finally:
        stop_event.set()
        heartbeat.join(timeout=2)

    return {"status": get_current_status(job_id)}


def get_current_status(job_id: str) -> str:
    with session_scope() as db:
        job = db.get(JobRecord, job_id)
        return job.status if job is not None else "missing"


def recover_stale_jobs() -> int:
    cutoff = _now().timestamp() - settings.job_stale_after_seconds
    recovered = 0
    with session_scope() as db:
        candidates = db.scalars(
            select(JobRecord)
            .where(JobRecord.status.in_(("running", "cancel_requested")))
            .with_for_update(skip_locked=True)
        ).all()
        for job in candidates:
            heartbeat_at = job.heartbeat_at or job.started_at
            if heartbeat_at is None:
                continue
            if heartbeat_at.tzinfo is None:
                heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
            if heartbeat_at.timestamp() > cutoff:
                continue
            if job.status == "cancel_requested" or job.cancel_requested:
                job.status = "cancelled"
                job.finished_at = _now()
                job.progress_stage = "cancelled"
                job.progress_message = (
                    "Worker heartbeat expired after cancellation; marked cancelled."
                )
                append_event(db, job, "cancelled", job.progress_message)
            elif job.attempt_count >= job.max_attempts:
                job.status = "failed"
                job.finished_at = _now()
                job.error_code = "WORKER_HEARTBEAT_EXPIRED"
                job.error_message = "Worker heartbeat expired and no attempts remain."
                job.progress_stage = "failed"
                job.progress_message = "Worker was lost and the attempt budget is exhausted."
                append_event(db, job, "failed", job.progress_message)
            else:
                job.status = "queued"
                job.dispatch_generation += 1
                job.progress_stage = "recovering"
                job.progress_message = "Worker heartbeat expired; job safely re-queued."
                job.heartbeat_at = None
                append_event(db, job, "recovered", job.progress_message)
                db.add(
                    OutboxRecord(
                        id=str(uuid4()),
                        job_id=job.id,
                        dispatch_generation=job.dispatch_generation,
                        queue_name=job.queue_name,
                        status="pending",
                        attempts=0,
                        available_at=_now(),
                        created_at=_now(),
                    )
                )
            if job.status in {"failed", "cancelled"}:
                from app.services.workflow_engine import advance_workflow_for_job

                advance_workflow_for_job(db, job)
            elif job.status == "queued":
                from app.services.workflow_engine import mark_workflow_job_queued

                mark_workflow_job_queued(db, job)
            recovered += 1
    return recovered
