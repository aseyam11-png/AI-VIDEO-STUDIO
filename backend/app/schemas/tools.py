from typing import Annotated, Literal  # noqa: I001
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


JobToolId = Literal[
    "media.proxy",
    "media.scenes",
    "media.timeline",
    "media.render",
    "speech.transcribe",
    "audio.enhance",
    "vision.detect",
    "vision.track",
    "video.generate",
    "video.generate_sequence",
    "video.enhance",
    "video.enhance",
    "video.upscale",
    "video.interpolate",
    "motion.blender",
]


class DirectorPlanToolCall(BaseModel):
    """Synchronous planning call; it never starts model jobs or renders media."""

    model_config = ConfigDict(extra="forbid")

    tool_id: Literal["director.plan"]
    brief: str = Field(min_length=20, max_length=12000)
    target_duration_seconds: int = Field(default=60, ge=15, le=300)
    language: Literal["ar", "en"] = "ar"


class AsyncJobToolCall(BaseModel):
    """One typed operation submitted to the durable job engine."""

    model_config = ConfigDict(extra="forbid")

    tool_id: JobToolId
    project_id: UUID
    asset_id: UUID
    payload: dict = Field(default_factory=dict)
    idempotency_key: str = Field(
        min_length=8,
        max_length=160,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


ToolExecutionRequest = Annotated[
    DirectorPlanToolCall | AsyncJobToolCall,
    Field(discriminator="tool_id"),
]
