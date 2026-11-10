from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class JobType(StrEnum):
    MEDIA_PROXY = "media.proxy"
    MEDIA_SCENES = "media.scenes"
    MEDIA_TIMELINE = "media.timeline"
    MEDIA_RENDER = "media.render"
    SPEECH_TRANSCRIBE = "speech.transcribe"
    AUDIO_ENHANCE = "audio.enhance"
    VISION_DETECT = "vision.detect"
    VISION_TRACK = "vision.track"
    VIDEO_GENERATE = "video.generate"
    VIDEO_GENERATE_SEQUENCE = "video.generate_sequence"
    VIDEO_ENHANCE = "video.enhance"
    VIDEO_UPSCALE = "video.upscale"
    VIDEO_INTERPOLATE = "video.interpolate"
    MOTION_BLENDER = "motion.blender"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    asset_id: UUID
    job_type: JobType
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")


class JobView(BaseModel):
    id: UUID
    project_id: UUID
    asset_id: UUID | None
    job_type: JobType
    queue_name: str
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    progress_stage: str
    progress_message: str
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    attempt_count: int
    max_attempts: int
    retry_count: int
    dispatch_generation: int
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    heartbeat_at: datetime | None
    finished_at: datetime | None


class JobEventView(BaseModel):
    id: int
    job_id: UUID
    event_type: str
    status: JobStatus
    progress: int
    message: str
    details: dict[str, Any]
    created_at: datetime


class JobListView(BaseModel):
    items: list[JobView]
    limit: int
    offset: int
    total: int
