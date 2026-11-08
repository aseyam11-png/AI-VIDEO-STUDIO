from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.models import CreativePlan


class WorkflowCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    asset_id: UUID
    idempotency_key: str = Field(
        min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    brief: str = Field(min_length=20, max_length=12000)
    language: str = Field(default="ar", pattern=r"^(ar|en)$")
    creative_plan: CreativePlan
    transcribe_audio: bool = False
    enhance_audio: bool = False
    detection_prompt: str | None = Field(default=None, min_length=1, max_length=500)
    tracking_prompt: str | None = Field(default=None, min_length=1, max_length=500)
    upscale_output: bool = False
    generate_video: bool = False
    interpolate_generated_video: bool = False

    @model_validator(mode="after")
    def validate_workflow_options(self):
        if self.interpolate_generated_video and not self.generate_video:
            raise ValueError("Frame interpolation requires video generation in this workflow.")
        return self



class WorkflowFromBriefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    asset_id: UUID
    idempotency_key: str = Field(
        min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    brief: str = Field(min_length=20, max_length=12000)
    language: str = Field(default="ar", pattern=r"^(ar|en)$")
    target_duration_seconds: int = Field(default=60, ge=15, le=300)
    transcribe_audio: bool = False
    enhance_audio: bool = False
    detection_prompt: str | None = Field(default=None, min_length=1, max_length=500)
    tracking_prompt: str | None = Field(default=None, min_length=1, max_length=500)
    upscale_output: bool = False
    generate_video: bool = False
    interpolate_generated_video: bool = False

    @model_validator(mode="after")
    def validate_workflow_options(self):
        if self.interpolate_generated_video and not self.generate_video:
            raise ValueError("Frame interpolation requires video generation in this workflow.")
        return self
