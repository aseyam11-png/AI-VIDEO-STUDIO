from typing import Literal
from uuid import UUID

from app.schemas.models import CreativePlan
from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    product_identity_lock: bool = False
    generation_provider: Literal["auto", "local_wan", "seedance2_5"] = "auto"
    allow_external_processing: bool = False

    @model_validator(mode="after")
    def validate_workflow_options(self):
        if self.interpolate_generated_video and not self.generate_video:
            raise ValueError("Frame interpolation requires video generation in this workflow.")
        if self.product_identity_lock and not self.generate_video:
            raise ValueError("Product identity lock requires video generation from an image.")
        if self.allow_external_processing and not self.generate_video:
            raise ValueError("External processing consent is valid only for video generation.")
        if self.generation_provider == "seedance2_5" and not self.allow_external_processing:
            raise ValueError("Seedance 2.5 requires explicit external-processing consent.")
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
    target_duration_seconds: int = Field(default=60, ge=1, le=300)
    transcribe_audio: bool = False
    enhance_audio: bool = False
    detection_prompt: str | None = Field(default=None, min_length=1, max_length=500)
    tracking_prompt: str | None = Field(default=None, min_length=1, max_length=500)
    upscale_output: bool = False
    generate_video: bool = False
    interpolate_generated_video: bool = False
    product_identity_lock: bool = False
    generation_provider: Literal["auto", "local_wan", "seedance2_5"] = "auto"
    allow_external_processing: bool = False

    @model_validator(mode="after")
    def validate_workflow_options(self):
        if self.interpolate_generated_video and not self.generate_video:
            raise ValueError("Frame interpolation requires video generation in this workflow.")
        if self.product_identity_lock and not self.generate_video:
            raise ValueError("Product identity lock requires video generation from an image.")
        if self.allow_external_processing and not self.generate_video:
            raise ValueError("External processing consent is valid only for video generation.")
        if self.generation_provider == "seedance2_5" and not self.allow_external_processing:
            raise ValueError("Seedance 2.5 requires explicit external-processing consent.")
        return self



class ProductIdentityReviewChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    silhouette_and_proportions: bool = False
    colors_and_materials: bool = False
    logo_and_labels: bool = False
    components_and_packaging: bool = False
    consistency_across_shots: bool = False


class WorkflowIdentityReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    notes: str = Field(default="", max_length=2000)
    checks: ProductIdentityReviewChecks = Field(default_factory=ProductIdentityReviewChecks)

    @model_validator(mode="after")
    def require_completed_checklist_for_approval(self):
        if self.approved and not all(self.checks.model_dump().values()):
            raise ValueError("All five product identity checks must be confirmed before approval.")
        return self
