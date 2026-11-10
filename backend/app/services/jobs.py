import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from app.core.config import settings
from app.db.models import (
    AssetRecord,
    JobEventRecord,
    JobRecord,
    OutboxRecord,
    ProjectRecord,
    WorkflowStepRecord,
)
from app.db.session import session_scope
from app.schemas.jobs import JobCreate, JobType
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError


class JobNotFoundError(LookupError):
    pass


class JobConflictError(RuntimeError):
    pass


class JobRequestError(ValueError):
    pass


class _StrictOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _ProxyOptions(_StrictOptions):
    max_width: int = Field(default=1280, ge=160, le=1920)
    fps: int = Field(default=30, ge=1, le=60)


class _SceneOptions(_StrictOptions):
    threshold: float = Field(default=27.0, gt=0, le=100)
    min_scene_len: int = Field(default=15, ge=1, le=1000)


class _TimelineOptions(_StrictOptions):
    fps: int = Field(default=30, ge=1, le=120)


class _RenderOptions(_StrictOptions):
    width: int = Field(default=1920, ge=320, le=3840)
    height: int = Field(default=1080, ge=240, le=2160)
    fps: int = Field(default=30, ge=1, le=60)
    crf: int = Field(default=20, ge=0, le=35)


class _SpeechOptions(_StrictOptions):
    model_name: str = Field(default="small", pattern=r"^small$")
    language: str = Field(default="ar", pattern=r"^(ar|en)$")
    align: bool = False


class _AudioEnhanceOptions(_StrictOptions):
    post_filter: bool = False


class _VisionDetectOptions(_StrictOptions):
    prompt: str = Field(min_length=1, max_length=500)
    box_threshold: float = Field(default=0.35, ge=0.05, le=0.95)
    text_threshold: float = Field(default=0.25, ge=0.05, le=0.95)


class _VisionTrackOptions(_VisionDetectOptions):
    max_frames: int = Field(default=300, ge=1, le=2000)


class _VideoGenerateOptions(_StrictOptions):
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str = Field(default="", max_length=2000)
    duration_seconds: int = Field(default=5, ge=1, le=10)
    width: int = Field(default=832, ge=256, le=1280, multiple_of=16)
    height: int = Field(default=480, ge=256, le=1280, multiple_of=16)
    approved: bool = False


class _VideoShotOptions(_StrictOptions):
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str = Field(default="", max_length=2000)
    duration_seconds: int = Field(ge=1, le=10)


class _VideoSequenceOptions(_StrictOptions):
    shots: list[_VideoShotOptions] = Field(min_length=2, max_length=8)
    generation_width: int = Field(default=832, ge=256, le=1280, multiple_of=16)
    generation_height: int = Field(default=480, ge=256, le=1280, multiple_of=16)
    output_width: int = Field(default=1920, ge=320, le=3840)
    output_height: int = Field(default=1080, ge=240, le=2160)
    fps: int = Field(default=30, ge=1, le=60)
    crf: int = Field(default=20, ge=0, le=35)
    interpolate_factor: Literal[1, 2] = 1
    approved: bool = False


class _VideoUpscaleOptions(_StrictOptions):
    scale: int = Field(default=2, ge=2, le=4)


class _VideoInterpolateOptions(_StrictOptions):
    factor: int = Field(default=2, ge=2, le=4)


class _BlenderOptions(_StrictOptions):
    template_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    frame_start: int = Field(default=1, ge=1, le=10000)
    frame_end: int = Field(default=250, ge=1, le=10000)
    width: int = Field(default=1920, ge=320, le=3840)
    height: int = Field(default=1080, ge=240, le=2160)
    fps: Literal[24, 25, 30, 50, 60] = 30
    approved: bool = False


_OPTION_MODELS = {
    JobType.MEDIA_PROXY: _ProxyOptions,
    JobType.MEDIA_SCENES: _SceneOptions,
    JobType.MEDIA_TIMELINE: _TimelineOptions,
    JobType.MEDIA_RENDER: _RenderOptions,
    JobType.SPEECH_TRANSCRIBE: _SpeechOptions,
    JobType.AUDIO_ENHANCE: _AudioEnhanceOptions,
    JobType.VISION_DETECT: _VisionDetectOptions,
    JobType.VISION_TRACK: _VisionTrackOptions,
    JobType.VIDEO_GENERATE: _VideoGenerateOptions,
    JobType.VIDEO_GENERATE_SEQUENCE: _VideoSequenceOptions,
    JobType.VIDEO_UPSCALE: _VideoUpscaleOptions,
    JobType.VIDEO_INTERPOLATE: _VideoInterpolateOptions,
    JobType.MOTION_BLENDER: _BlenderOptions,
}

_SPEECH_QUEUE_TYPES = {JobType.SPEECH_TRANSCRIBE}

_MODEL_QUEUE_TYPES = {
    JobType.VISION_DETECT,
    JobType.VISION_TRACK,
    JobType.VIDEO_GENERATE,
    JobType.VIDEO_GENERATE_SEQUENCE,
    JobType.VIDEO_UPSCALE,
    JobType.VIDEO_INTERPOLATE,
    JobType.MOTION_BLENDER,
}

_VIDEO_REQUIRED_TYPES = {
    JobType.MEDIA_PROXY,
    JobType.MEDIA_SCENES,
    JobType.MEDIA_TIMELINE,
    JobType.MEDIA_RENDER,
    JobType.SPEECH_TRANSCRIBE,
    JobType.AUDIO_ENHANCE,
    JobType.VISION_TRACK,
    JobType.VIDEO_INTERPOLATE,
}


def normalize_payload(job_type: JobType, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        normalized = _OPTION_MODELS[job_type].model_validate(payload).model_dump()
    except ValidationError as exc:
        raise JobRequestError(exc.errors(include_url=False)) from exc
    if job_type in {
        JobType.VIDEO_GENERATE,
        JobType.VIDEO_GENERATE_SEQUENCE,
        JobType.MOTION_BLENDER,
    } and not normalized["approved"]:
        raise JobRequestError("This operation requires explicit user approval.")
    if job_type == JobType.SPEECH_TRANSCRIBE and not settings.speech_worker_enabled:
        raise JobRequestError(
            "WhisperX worker is disabled. Start the speech Compose profile and set "
            "SPEECH_WORKER_ENABLED=true before submitting transcription jobs."
        )
    if job_type == JobType.AUDIO_ENHANCE and not (
        settings.deepfilter_script and settings.deepfilter_model_dir
    ):
        raise JobRequestError(
            "DeepFilterNet is not configured. Set DEEPFILTER_SCRIPT and "
            "DEEPFILTER_MODEL_DIR to an installed script and existing checkpoint directory."
        )
    if (
        job_type in (_MODEL_QUEUE_TYPES - {JobType.MOTION_BLENDER})
        and not settings.comfyui_base_url
    ):
        raise JobRequestError(
            "COMFYUI_BASE_URL must be configured before submitting ComfyUI model jobs."
        )
    if job_type == JobType.MOTION_BLENDER and not settings.blender_executable:
        raise JobRequestError(
            "BLENDER_EXECUTABLE must point to Blender inside the model worker environment."
        )
    if job_type == JobType.VIDEO_GENERATE_SEQUENCE:
        shots = normalized["shots"]
        total_duration = sum(shot["duration_seconds"] for shot in shots)
        if not 2 <= len(shots) <= 8 or total_duration > 80:
            raise JobRequestError("A generated sequence supports 2–8 shots and at most 80 seconds.")
        try:
            from app.services.job_policy import validate_generation_request

            for shot in shots:
                validate_generation_request(
                    {
                        "duration_seconds": shot["duration_seconds"],
                        "workflow_id": "wan22_ti2v_5b_v1",
                    },
                    approved=normalized["approved"],
                )
        except (PermissionError, ValueError) as exc:
            raise JobRequestError(str(exc)) from exc
    if job_type == JobType.VIDEO_GENERATE:
        try:
            from app.services.job_policy import validate_generation_request

            validate_generation_request(
                {
                    "duration_seconds": normalized["duration_seconds"],
                    "workflow_id": "wan22_ti2v_5b_v1",
                },
                approved=normalized["approved"],
            )
        except (PermissionError, ValueError) as exc:
            raise JobRequestError(str(exc)) from exc
    if job_type == JobType.MOTION_BLENDER and normalized["frame_end"] < normalized["frame_start"]:
        raise JobRequestError("Blender frame_end must be greater than or equal to frame_start.")
    return normalized


def _now() -> datetime:
    return datetime.now(timezone.utc)


def job_to_dict(job: JobRecord) -> dict[str, Any]:
    return {
        "id": job.id,
        "project_id": job.project_id,
        "asset_id": job.asset_id,
        "job_type": job.job_type,
        "queue_name": job.queue_name,
        "status": job.status,
        "progress": job.progress,
        "progress_stage": job.progress_stage,
        "progress_message": job.progress_message,
        "payload": job.payload_json or {},
        "result": job.result_json,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "retry_count": job.retry_count,
        "dispatch_generation": job.dispatch_generation,
        "cancel_requested": job.cancel_requested,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "started_at": job.started_at,
        "heartbeat_at": job.heartbeat_at,
        "finished_at": job.finished_at,
    }


def event_to_dict(event: JobEventRecord) -> dict[str, Any]:
    return {
        "id": event.id,
        "job_id": event.job_id,
        "event_type": event.event_type,
        "status": event.status,
        "progress": event.progress,
        "message": event.message,
        "details": event.details_json or {},
        "created_at": event.created_at,
    }


def append_event(
    db,
    job: JobRecord,
    event_type: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        JobEventRecord(
            job_id=job.id,
            event_type=event_type,
            status=job.status,
            progress=job.progress,
            message=message[:500],
            details_json=details or {},
            created_at=_now(),
        )
    )


def _create_outbox(db, job: JobRecord) -> None:
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


def _request_hash(data: JobCreate, normalized_payload: dict[str, Any]) -> str:
    canonical = {
        "project_id": str(data.project_id),
        "asset_id": str(data.asset_id),
        "job_type": data.job_type.value,
        "payload": normalized_payload,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def create_job(data: JobCreate) -> tuple[dict[str, Any], bool]:
    normalized = normalize_payload(data.job_type, data.payload)
    digest = _request_hash(data, normalized)

    try:
        with session_scope() as db:
            project = db.get(ProjectRecord, str(data.project_id))
            if project is None:
                raise JobRequestError(
                    "Project metadata is not in the database. Import legacy manifests first."
                )
            asset = db.scalar(
                select(AssetRecord)
                .where(AssetRecord.id == str(data.asset_id))
                .with_for_update()
            )
            if asset is None or asset.project_id != str(data.project_id):
                raise JobRequestError("Asset was not found in the requested project.")
            if data.job_type in _VIDEO_REQUIRED_TYPES and asset.asset_type != "video":
                raise JobRequestError("This job type requires a video asset.")
            if data.job_type in {
                JobType.VIDEO_GENERATE,
                JobType.VIDEO_GENERATE_SEQUENCE,
            } and asset.asset_type != "image":
                raise JobRequestError(
                    "Wan 2.2 generation requires an image reference asset."
                )
            if asset.asset_type not in {"video", "image"}:
                raise JobRequestError("The selected asset type is not supported for model jobs.")

            existing = db.scalar(
                select(JobRecord).where(
                    JobRecord.project_id == str(data.project_id),
                    JobRecord.idempotency_key == data.idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_hash != digest:
                    raise JobConflictError(
                        "Idempotency key was already used with a different request."
                    )
                return job_to_dict(existing), False

            active_job = db.scalar(
                select(JobRecord).where(
                    JobRecord.project_id == str(data.project_id),
                    JobRecord.asset_id == str(data.asset_id),
                    JobRecord.job_type == data.job_type.value,
                    JobRecord.status.in_(("queued", "running", "cancel_requested")),
                )
            )
            if active_job is not None:
                raise JobConflictError(
                    "An active job of this type already exists for this asset."
                )

            now = _now()
            job = JobRecord(
                id=str(uuid4()),
                project_id=str(data.project_id),
                asset_id=str(data.asset_id),
                job_type=data.job_type.value,
                queue_name=(
                    "speech" if data.job_type in _SPEECH_QUEUE_TYPES
                    else "models" if data.job_type in _MODEL_QUEUE_TYPES
                    else "cpu"
                ),
                status="queued",
                progress=0,
                progress_stage="queued",
                progress_message="Accepted and waiting for a worker.",
                payload_json=normalized,
                result_json=None,
                error_code=None,
                error_message=None,
                idempotency_key=data.idempotency_key,
                request_hash=digest,
                attempt_count=0,
                max_attempts=settings.job_default_max_attempts,
                retry_count=0,
                dispatch_generation=1,
                cancel_requested=False,
                created_at=now,
                updated_at=now,
            )
            db.add(job)
            db.flush()
            append_event(db, job, "created", "Job accepted and queued.")
            _create_outbox(db, job)
            return job_to_dict(job), True
    except IntegrityError as exc:
        # A concurrent duplicate can win the unique constraint between lookup and insert.
        with session_scope() as db:
            existing = db.scalar(
                select(JobRecord).where(
                    JobRecord.project_id == str(data.project_id),
                    JobRecord.idempotency_key == data.idempotency_key,
                )
            )
            if existing is None:
                active = db.scalar(
                    select(JobRecord).where(
                        JobRecord.project_id == str(data.project_id),
                        JobRecord.asset_id == str(data.asset_id),
                        JobRecord.job_type == data.job_type.value,
                        JobRecord.status.in_(("queued", "running", "cancel_requested")),
                    )
                )
                if active is not None:
                    raise JobConflictError(
                        "An active job of this type already exists for this asset."
                    ) from exc
                raise
            if existing.request_hash != digest:
                raise JobConflictError(
                    "Idempotency key was already used with a different request."
                ) from exc
            return job_to_dict(existing), False


def get_job(job_id: str) -> dict[str, Any]:
    with session_scope() as db:
        job = db.get(JobRecord, job_id)
        if job is None:
            raise JobNotFoundError("Job not found.")
        return job_to_dict(job)


def list_jobs(
    *,
    project_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    with session_scope() as db:
        query = select(JobRecord)
        count_query = select(func.count()).select_from(JobRecord)
        if project_id:
            query = query.where(JobRecord.project_id == project_id)
            count_query = count_query.where(JobRecord.project_id == project_id)
        if status:
            query = query.where(JobRecord.status == status)
            count_query = count_query.where(JobRecord.status == status)
        total = db.scalar(count_query) or 0
        items = db.scalars(
            query.order_by(JobRecord.created_at.desc(), JobRecord.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return {
            "items": [job_to_dict(item) for item in items],
            "limit": limit,
            "offset": offset,
            "total": total,
        }


def cancel_job(job_id: str) -> dict[str, Any]:
    with session_scope() as db:
        job = db.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
        if job is None:
            raise JobNotFoundError("Job not found.")
        if job.status == "queued":
            job.status = "cancelled"
            job.cancel_requested = True
            job.finished_at = _now()
            job.progress_stage = "cancelled"
            job.progress_message = "Cancelled before a worker started."
            append_event(db, job, "cancelled", job.progress_message)
        elif job.status == "running":
            job.status = "cancel_requested"
            job.cancel_requested = True
            job.progress_stage = "cancelling"
            job.progress_message = (
                "Cancellation requested; waiting for the active media step to stop."
            )
            append_event(db, job, "cancel_requested", job.progress_message)
        elif job.status in {"cancel_requested", "cancelled"}:
            pass
        else:
            raise JobConflictError(f"Cannot cancel a job in state '{job.status}'.")
        if job.status == "cancelled":
            from app.services.workflow_engine import advance_workflow_for_job

            advance_workflow_for_job(db, job)
        return job_to_dict(job)


def retry_job(job_id: str) -> dict[str, Any]:
    try:
        with session_scope() as db:
            job = db.scalar(
                select(JobRecord).where(JobRecord.id == job_id).with_for_update()
            )
            if job is None:
                raise JobNotFoundError("Job not found.")
            if job.status not in {"failed", "cancelled"}:
                raise JobConflictError("Only failed or cancelled jobs can be retried.")
            if job.attempt_count >= job.max_attempts:
                raise JobConflictError("The configured attempt budget has been exhausted.")

            active = db.scalar(
                select(JobRecord).where(
                    JobRecord.project_id == job.project_id,
                    JobRecord.asset_id == job.asset_id,
                    JobRecord.job_type == job.job_type,
                    JobRecord.id != job.id,
                    JobRecord.status.in_(("queued", "running", "cancel_requested")),
                )
            )
            if active is not None:
                raise JobConflictError(
                    "Another active job of this type already exists for this asset."
                )

            job.status = "queued"
            job.cancel_requested = False
            job.progress = 0
            job.progress_stage = "queued"
            job.progress_message = "Retry accepted; waiting for a worker."
            job.result_json = None
            job.error_code = None
            job.error_message = None
            job.finished_at = None
            job.heartbeat_at = None
            job.retry_count += 1
            job.dispatch_generation += 1
            job.updated_at = _now()
            append_event(
                db,
                job,
                "retry_queued",
                "Job retry accepted.",
                details={"retry_count": job.retry_count},
            )
            _create_outbox(db, job)
            workflow_step = db.scalar(
                select(WorkflowStepRecord).where(WorkflowStepRecord.job_id == job.id)
            )
            if workflow_step is not None:
                from app.db.models import WorkflowRunRecord

                workflow_step.status = "queued"
                workflow_step.error_message = None
                workflow_step.finished_at = None
                workflow_run = db.get(WorkflowRunRecord, workflow_step.workflow_id)
                if workflow_run is not None:
                    workflow_run.status = "queued"
                    workflow_run.error_message = None
                    workflow_run.finished_at = None
            result = job_to_dict(job)
        return result
    except IntegrityError as exc:
        raise JobConflictError(
            "Another active job of this type already exists for this asset."
        ) from exc


def get_job_events(job_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    with session_scope() as db:
        if db.get(JobRecord, job_id) is None:
            raise JobNotFoundError("Job not found.")
        events = db.scalars(
            select(JobEventRecord)
            .where(JobEventRecord.job_id == job_id)
            .order_by(JobEventRecord.id)
            .limit(limit)
        ).all()
        return [event_to_dict(event) for event in events]
