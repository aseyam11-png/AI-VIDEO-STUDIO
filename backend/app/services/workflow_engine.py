import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    AssetRecord,
    JobRecord,
    ProjectRecord,
    WorkflowRunRecord,
    WorkflowStepRecord,
)
from app.db.session import session_scope
from app.integrations.ollama import generate_creative_plan
from app.schemas.jobs import JobType
from app.schemas.models import CreativePlan
from app.schemas.workflows import WorkflowCreateRequest, WorkflowFromBriefRequest
from app.services.jobs import (
    JobRequestError,
    _create_outbox,
    append_event,
    normalize_payload,
    job_to_dict,
)


class WorkflowNotFoundError(LookupError):
    pass


class WorkflowConflictError(RuntimeError):
    pass


class WorkflowRequestError(ValueError):
    pass


_TERMINAL_WORKFLOW_STATES = {"succeeded", "failed", "cancelled"}
_MODEL_QUEUE_TYPES = {
    JobType.VISION_DETECT,
    JobType.VISION_TRACK,
    JobType.VIDEO_GENERATE,
    JobType.VIDEO_UPSCALE,
    JobType.VIDEO_INTERPOLATE,
    JobType.MOTION_BLENDER,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _request_digest(payload: dict) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _existing_workflow(
    project_id: str,
    idempotency_key: str,
    request_hash: str,
) -> dict | None:
    with session_scope() as db:
        run = db.scalar(
            select(WorkflowRunRecord).where(
                WorkflowRunRecord.project_id == project_id,
                WorkflowRunRecord.idempotency_key == idempotency_key,
            )
        )
        if run is None:
            return None
        if run.request_hash != request_hash:
            raise WorkflowConflictError(
                "Workflow idempotency key was already used with a different request."
            )
        return _workflow_to_dict(db, run)


def _queue_for(job_type: JobType) -> str:
    if job_type == JobType.SPEECH_TRANSCRIBE:
        return "speech"
    if job_type in _MODEL_QUEUE_TYPES:
        return "models"
    return "cpu"


def _resolution(value: str) -> tuple[int, int]:
    try:
        width_text, height_text = value.lower().split("x", maxsplit=1)
        width, height = int(width_text), int(height_text)
    except (ValueError, AttributeError) as exc:
        raise WorkflowRequestError(
            "Project target_resolution must look like 1920x1080."
        ) from exc
    if not (320 <= width <= 3840 and 240 <= height <= 2160):
        raise WorkflowRequestError("Project render resolution is outside supported limits.")
    return width, height


def _step_specs(
    asset_type: str,
    request: WorkflowCreateRequest,
    project: ProjectRecord,
) -> list[tuple[JobType, dict]]:
    plan = request.creative_plan
    fps = min(project.target_fps, 60)
    width, height = _resolution(project.target_resolution)
    steps: list[tuple[JobType, dict]] = []

    if request.detection_prompt:
        steps.append(
            (
                JobType.VISION_DETECT,
                {
                    "prompt": request.detection_prompt,
                    "box_threshold": 0.35,
                    "text_threshold": 0.25,
                },
            )
        )

    if request.tracking_prompt:
        if asset_type != "video":
            raise WorkflowRequestError("SAM 2 tracking requires a video source asset.")
        steps.append(
            (
                JobType.VISION_TRACK,
                {
                    "prompt": request.tracking_prompt,
                    "box_threshold": 0.35,
                    "text_threshold": 0.25,
                    "max_frames": 300,
                },
            )
        )

    if asset_type == "image":
        if not request.generate_video:
            raise WorkflowRequestError(
                "An image workflow must explicitly enable video generation."
            )
        scene = plan.scenes[0]
        prompt = (
            f"{plan.logline}. Style: {plan.visual_style}. "
            f"Scene: {scene.visual_description}. "
            f"Camera: {scene.camera_movement}. Audience: {plan.audience}."
        )[:4000]
        steps.append(
            (
                JobType.VIDEO_GENERATE,
                {
                    "prompt": prompt,
                    "negative_prompt": (
                        "low quality, flicker, distorted geometry, unreadable text"
                    ),
                    "duration_seconds": min(10, max(1, scene.duration_seconds)),
                    "width": 832,
                    "height": 480,
                    "approved": True,
                },
            )
        )
        if request.interpolate_generated_video:
            steps.append((JobType.VIDEO_INTERPOLATE, {"factor": 2}))
        if request.upscale_output:
            steps.append((JobType.VIDEO_UPSCALE, {"scale": 2}))
    elif asset_type == "video":
        if request.generate_video:
            raise WorkflowRequestError(
                "Video generation is an image-to-video operation; source asset is already a video."
            )
        if request.enhance_audio:
            steps.append((JobType.AUDIO_ENHANCE, {"post_filter": False}))
        if request.transcribe_audio:
            steps.append(
                (
                    JobType.SPEECH_TRANSCRIBE,
                    {
                        "model_name": "small",
                        "language": request.language,
                        "align": False,
                    },
                )
            )
        steps.append((JobType.MEDIA_PROXY, {"max_width": 1280, "fps": fps}))
        steps.append((JobType.MEDIA_SCENES, {"threshold": 27.0, "min_scene_len": 15}))
        if request.upscale_output:
            steps.append((JobType.VIDEO_UPSCALE, {"scale": 2}))
    else:
        raise WorkflowRequestError("Workflows support image or video source assets only.")

    steps.extend(
        [
            (JobType.MEDIA_TIMELINE, {"fps": fps}),
            (
                JobType.MEDIA_RENDER,
                {"width": width, "height": height, "fps": fps, "crf": 20},
            ),
        ]
    )
    return steps


def _workflow_to_dict(db: Session, run: WorkflowRunRecord) -> dict:
    steps = db.scalars(
        select(WorkflowStepRecord)
        .where(WorkflowStepRecord.workflow_id == run.id)
        .order_by(WorkflowStepRecord.sequence)
    ).all()
    return {
        "id": run.id,
        "project_id": run.project_id,
        "asset_id": run.asset_id,
        "brief": run.brief,
        "language": run.language,
        "creative_plan": run.plan_json,
        "options": run.options_json,
        "status": run.status,
        "current_step_index": run.current_step_index,
        "error_message": run.error_message,
        "approved_at": run.approved_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "finished_at": run.finished_at,
        "steps": [
            {
                "id": step.id,
                "sequence": step.sequence,
                "tool_id": step.tool_id,
                "asset_id": step.asset_id,
                "status": step.status,
                "job_id": step.job_id,
                "result": step.result_json,
                "error_message": step.error_message,
                "started_at": step.started_at,
                "finished_at": step.finished_at,
            }
            for step in steps
        ],
    }


def create_workflow_run(
    request: WorkflowCreateRequest,
    *,
    request_hash_override: str | None = None,
) -> dict:
    request_hash = request_hash_override or _request_digest(
        request.model_dump(mode="json", exclude={"idempotency_key"})
    )
    project_id = str(request.project_id)
    existing = _existing_workflow(
        project_id, request.idempotency_key, request_hash
    )
    if existing is not None:
        return existing

    try:
        with session_scope() as db:
        project = db.get(ProjectRecord, str(request.project_id))
        if project is None:
            raise WorkflowRequestError(
                "Project metadata is not in PostgreSQL. Import legacy manifests first."
            )
        asset = db.scalar(
            select(AssetRecord).where(
                AssetRecord.project_id == str(request.project_id),
                AssetRecord.id == str(request.asset_id),
            )
        )
        if asset is None:
            raise WorkflowRequestError("Source asset does not belong to the requested project.")

        specs = _step_specs(asset.asset_type, request, project)
        normalized_specs = []
        for job_type, payload in specs:
            try:
                normalized = normalize_payload(job_type, payload)
            except JobRequestError as exc:
                raise WorkflowRequestError(
                    f"Workflow step '{job_type.value}' is not configured: {exc}"
                ) from exc
            normalized_specs.append((job_type, normalized))

        now = _now()
        run = WorkflowRunRecord(
            id=str(uuid4()),
            project_id=str(request.project_id),
            asset_id=str(request.asset_id),
            idempotency_key=request.idempotency_key,
            request_hash=request_hash,
            brief=request.brief,
            language=request.language,
            plan_json=request.creative_plan.model_dump(),
            options_json={
                "transcribe_audio": request.transcribe_audio,
                "enhance_audio": request.enhance_audio,
                "detection_prompt": request.detection_prompt,
                "tracking_prompt": request.tracking_prompt,
                "upscale_output": request.upscale_output,
                "generate_video": request.generate_video,
                "interpolate_generated_video": request.interpolate_generated_video,
            },
            status="awaiting_approval",
            current_step_index=0,
            created_at=now,
            updated_at=now,
        )
        db.add(run)
        db.flush()
        for index, (job_type, payload) in enumerate(normalized_specs, start=1):
            db.add(
                WorkflowStepRecord(
                    id=str(uuid4()),
                    workflow_id=run.id,
                    sequence=index,
                    tool_id=job_type.value,
                    asset_id=asset.id,
                    payload_json=payload,
                    status="planned",
                    created_at=now,
                    updated_at=now,
                )
            )
        db.flush()
            return _workflow_to_dict(db, run)
    except IntegrityError:
        existing = _existing_workflow(
            project_id, request.idempotency_key, request_hash
        )
        if existing is not None:
            return existing
        raise


def _enqueue_step(db: Session, run: WorkflowRunRecord, step: WorkflowStepRecord) -> dict:
    if step.status not in {"planned", "failed"}:
        raise WorkflowConflictError(f"Cannot enqueue workflow step in state '{step.status}'.")
    asset = db.get(AssetRecord, step.asset_id)
    if asset is None or asset.project_id != run.project_id:
        raise WorkflowRequestError("Workflow step input asset is missing or out of scope.")
    job_type = JobType(step.tool_id)

    active = db.scalar(
        select(JobRecord).where(
            JobRecord.project_id == run.project_id,
            JobRecord.asset_id == step.asset_id,
            JobRecord.job_type == job_type.value,
            JobRecord.status.in_(("queued", "running", "cancel_requested")),
        )
    )
    if active is not None:
        raise WorkflowConflictError(
            f"Cannot start '{job_type.value}': an identical operation is already active."
        )

    normalized = normalize_payload(job_type, step.payload_json)
    idempotency_key = f"workflow-{run.id}-{step.sequence:03d}"
    canonical = {
        "project_id": run.project_id,
        "asset_id": step.asset_id,
        "job_type": job_type.value,
        "payload": normalized,
        "workflow_id": run.id,
        "workflow_step_id": step.id,
    }
    digest = hashlib.sha256(
        json.dumps(
            canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    now = _now()
    job = JobRecord(
        id=str(uuid4()),
        project_id=run.project_id,
        asset_id=step.asset_id,
        job_type=job_type.value,
        queue_name=_queue_for(job_type),
        status="queued",
        progress=0,
        progress_stage="queued",
        progress_message=f"Workflow step {step.sequence} accepted.",
        payload_json=normalized,
        result_json=None,
        error_code=None,
        error_message=None,
        idempotency_key=idempotency_key,
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
    append_event(
        db,
        job,
        "created",
        job.progress_message,
        details={"workflow_id": run.id, "workflow_step_id": step.id},
    )
    _create_outbox(db, job)
    step.job_id = job.id
    step.status = "queued"
    step.error_message = None
    step.started_at = None
    step.finished_at = None
    step.updated_at = now
    run.status = "queued"
    run.current_step_index = step.sequence
    run.error_message = None
    run.updated_at = now
    return job_to_dict(job)


def approve_workflow(workflow_id: str) -> dict:
    with session_scope() as db:
        run = db.scalar(
            select(WorkflowRunRecord)
            .where(WorkflowRunRecord.id == workflow_id)
            .with_for_update()
        )
        if run is None:
            raise WorkflowNotFoundError("Workflow not found.")
        if run.status != "awaiting_approval":
            if run.status in {"queued", "running", *_TERMINAL_WORKFLOW_STATES}:
                return _workflow_to_dict(db, run)
            raise WorkflowConflictError(f"Workflow cannot be approved in state '{run.status}'.")
        first_step = db.scalar(
            select(WorkflowStepRecord)
            .where(WorkflowStepRecord.workflow_id == run.id)
            .order_by(WorkflowStepRecord.sequence)
            .with_for_update()
        )
        if first_step is None:
            raise WorkflowRequestError("Workflow has no steps.")
        run.approved_at = _now()
        _enqueue_step(db, run, first_step)
        return _workflow_to_dict(db, run)


def mark_workflow_job_running(db: Session, job: JobRecord) -> None:
    step = db.scalar(
        select(WorkflowStepRecord)
        .where(WorkflowStepRecord.job_id == job.id)
        .with_for_update()
    )
    if step is None:
        return
    run = db.scalar(
        select(WorkflowRunRecord)
        .where(WorkflowRunRecord.id == step.workflow_id)
        .with_for_update()
    )
    if run is None or run.status in _TERMINAL_WORKFLOW_STATES:
        return
    now = _now()
    step.status = "running"
    step.started_at = step.started_at or now
    step.updated_at = now
    run.status = "running"
    run.updated_at = now


def advance_workflow_for_job(db: Session, job: JobRecord) -> None:
    step = db.scalar(
        select(WorkflowStepRecord)
        .where(WorkflowStepRecord.job_id == job.id)
        .with_for_update()
    )
    if step is None:
        return
    run = db.scalar(
        select(WorkflowRunRecord)
        .where(WorkflowRunRecord.id == step.workflow_id)
        .with_for_update()
    )
    if run is None or run.status in _TERMINAL_WORKFLOW_STATES:
        return

    now = _now()
    step.updated_at = now
    if job.status == "succeeded":
        step.status = "succeeded"
        step.result_json = job.result_json or {}
        step.error_message = None
        step.finished_at = now
        output_asset_id = (job.result_json or {}).get("output_asset_id")
        if run.status == "cancel_requested":
            run.status = "cancelled"
            run.finished_at = now
            run.updated_at = now
            return
        next_step = db.scalar(
            select(WorkflowStepRecord)
            .where(
                WorkflowStepRecord.workflow_id == run.id,
                WorkflowStepRecord.sequence > step.sequence,
                WorkflowStepRecord.status == "planned",
            )
            .order_by(WorkflowStepRecord.sequence)
            .with_for_update()
        )
        if next_step is None:
            run.status = "succeeded"
            run.finished_at = now
            run.error_message = None
            run.updated_at = now
            return
        if output_asset_id:
            output_asset = db.get(AssetRecord, str(output_asset_id))
            if output_asset is None or output_asset.project_id != run.project_id:
                step.status = "failed"
                step.error_message = "Previous step returned an invalid output asset reference."
                run.status = "failed"
                run.error_message = step.error_message
                run.finished_at = now
                run.updated_at = now
                return
            next_step.asset_id = output_asset.id
        try:
            _enqueue_step(db, run, next_step)
        except (WorkflowConflictError, WorkflowRequestError, JobRequestError) as exc:
            next_step.status = "failed"
            next_step.error_message = str(exc)[:2000]
            next_step.finished_at = now
            run.status = "failed"
            run.error_message = str(exc)[:2000]
            run.finished_at = now
            run.updated_at = now
    elif job.status == "failed":
        step.status = "failed"
        step.error_message = job.error_message or "Workflow job failed."
        step.finished_at = now
        run.status = "failed"
        run.error_message = step.error_message
        run.finished_at = now
        run.updated_at = now
    elif job.status == "cancelled":
        step.status = "cancelled"
        step.error_message = None
        step.finished_at = now
        run.status = "cancelled"
        run.error_message = None
        run.finished_at = now
        run.updated_at = now


def get_workflow(workflow_id: str) -> dict:
    with session_scope() as db:
        run = db.get(WorkflowRunRecord, workflow_id)
        if run is None:
            raise WorkflowNotFoundError("Workflow not found.")
        return _workflow_to_dict(db, run)


def cancel_workflow(workflow_id: str) -> dict:
    with session_scope() as db:
        run = db.scalar(
            select(WorkflowRunRecord)
            .where(WorkflowRunRecord.id == workflow_id)
            .with_for_update()
        )
        if run is None:
            raise WorkflowNotFoundError("Workflow not found.")
        if run.status in _TERMINAL_WORKFLOW_STATES:
            return _workflow_to_dict(db, run)
        now = _now()
        current = db.scalar(
            select(WorkflowStepRecord)
            .where(
                WorkflowStepRecord.workflow_id == run.id,
                WorkflowStepRecord.job_id.is_not(None),
                WorkflowStepRecord.status.in_(("queued", "running", "cancel_requested")),
            )
            .order_by(WorkflowStepRecord.sequence.desc())
            .with_for_update()
        )
        if current is None:
            run.status = "cancelled"
            run.finished_at = now
            run.updated_at = now
            return _workflow_to_dict(db, run)
        job = db.scalar(
            select(JobRecord).where(JobRecord.id == current.job_id).with_for_update()
        )
        if job is None or job.status in {"succeeded", "failed", "cancelled"}:
            run.status = "cancelled"
            run.finished_at = now
        elif job.status == "queued":
            job.status = "cancelled"
            job.cancel_requested = True
            job.finished_at = now
            job.progress_stage = "cancelled"
            job.progress_message = "Workflow cancelled before the step started."
            append_event(db, job, "cancelled", job.progress_message)
            current.status = "cancelled"
            current.finished_at = now
            run.status = "cancelled"
            run.finished_at = now
        else:
            job.status = "cancel_requested"
            job.cancel_requested = True
            job.progress_stage = "cancelling"
            job.progress_message = "Workflow cancellation requested."
            append_event(db, job, "cancel_requested", job.progress_message)
            current.status = "cancel_requested"
            run.status = "cancel_requested"
        run.updated_at = now
        return _workflow_to_dict(db, run)



def create_workflow_from_brief(request: WorkflowFromBriefRequest) -> dict:
    """Use Qwen to draft a plan, then persist a workflow that awaits explicit approval."""
    request_hash = _request_digest(
        request.model_dump(mode="json", exclude={"idempotency_key"})
    )
    existing = _existing_workflow(
        str(request.project_id), request.idempotency_key, request_hash
    )
    if existing is not None:
        existing["plan_model"] = settings.ollama_model
        existing["approval_required"] = True
        return existing

    plan = generate_creative_plan(
        request.brief,
        target_duration_seconds=request.target_duration_seconds,
        language=request.language,
    )
    create_request = WorkflowCreateRequest(
        project_id=request.project_id,
        asset_id=request.asset_id,
        idempotency_key=request.idempotency_key,
        brief=request.brief,
        language=request.language,
        creative_plan=CreativePlan.model_validate(plan.model_dump()),
        transcribe_audio=request.transcribe_audio,
        enhance_audio=request.enhance_audio,
        detection_prompt=request.detection_prompt,
        tracking_prompt=request.tracking_prompt,
        upscale_output=request.upscale_output,
        generate_video=request.generate_video,
        interpolate_generated_video=request.interpolate_generated_video,
    )
    result = create_workflow_run(create_request, request_hash_override=request_hash)
    result["plan_model"] = settings.ollama_model
    result["approval_required"] = True
    return result
