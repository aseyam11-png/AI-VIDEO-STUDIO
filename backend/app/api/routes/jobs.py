from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.schemas.jobs import JobCreate, JobEventView, JobListView, JobStatus, JobView
from app.services.jobs import (
    JobConflictError,
    JobNotFoundError,
    JobRequestError,
    cancel_job,
    create_job,
    get_job,
    get_job_events,
    list_jobs,
    retry_job,
)

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


@router.post("", response_model=JobView, status_code=status.HTTP_202_ACCEPTED)
def submit_job(payload: JobCreate, response: Response) -> dict:
    try:
        job, created = create_job(payload)
        if not created:
            response.headers["Idempotent-Replay"] = "true"
        return job
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except JobRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=JobListView)
def get_jobs(
    project_id: UUID | None = None,
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100000),
) -> dict:
    return list_jobs(
        project_id=str(project_id) if project_id else None,
        status=job_status.value if job_status else None,
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}", response_model=JobView)
def get_job_route(job_id: UUID) -> dict:
    try:
        return get_job(str(job_id))
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{job_id}/events", response_model=list[JobEventView])
def get_job_events_route(job_id: UUID, limit: int = Query(default=200, ge=1, le=500)) -> list[dict]:
    try:
        return get_job_events(str(job_id), limit=limit)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{job_id}/cancel", response_model=JobView)
def cancel_job_route(job_id: UUID) -> dict:
    try:
        return cancel_job(str(job_id))
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{job_id}/retry", response_model=JobView, status_code=status.HTTP_202_ACCEPTED)
def retry_job_route(job_id: UUID) -> dict:
    try:
        return retry_job(str(job_id))
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
