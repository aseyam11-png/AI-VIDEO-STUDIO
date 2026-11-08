from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_APPROVAL = "needs_approval"


class JobCreate(BaseModel):
    project_id: UUID
    job_type: str = Field(min_length=2, max_length=80)
    payload: dict = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=160)


class JobView(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    job_type: str
    status: JobStatus = JobStatus.QUEUED
    progress: float = Field(default=0, ge=0, le=100)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
