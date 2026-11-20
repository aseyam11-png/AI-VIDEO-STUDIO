from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CinematicDirection = Annotated[str, Field(min_length=3, max_length=300)]


class DirectorPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brief: str = Field(min_length=20, max_length=12000)
    target_duration_seconds: int = Field(default=60, ge=15, le=300)
    language: str = Field(default="ar", pattern=r"^(ar|en)$")


class CreativeScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1, le=20)
    duration_seconds: int = Field(ge=1, le=300)
    purpose: str = Field(min_length=3, max_length=300)
    visual_description: str = Field(min_length=3, max_length=1200)
    shot_type: str = Field(min_length=2, max_length=80)
    camera_movement: str = Field(min_length=2, max_length=120)
    narration: str = Field(default="", max_length=1200)
    on_screen_text: str = Field(default="", max_length=500)
    sound_design: str = Field(default="", max_length=500)
    focal_length_mm: int = Field(default=50, ge=14, le=135)
    aperture_f_number: float = Field(default=4.0, ge=1.2, le=22.0)
    depth_of_field: Literal["shallow", "moderate", "deep"] = "moderate"
    lighting_design: str = Field(default="", max_length=400)
    composition: str = Field(default="", max_length=400)
    subject_action: str = Field(default="", max_length=400)
    motion_intensity: Literal["static", "subtle", "controlled", "dynamic"] = "controlled"
    continuity_anchor: str = Field(default="", max_length=400)
    transition_intent: str = Field(default="", max_length=300)


class CreativePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=160)
    logline: str = Field(min_length=5, max_length=500)
    target_duration_seconds: int = Field(ge=1, le=300)
    audience: str = Field(min_length=2, max_length=300)
    visual_style: str = Field(min_length=3, max_length=500)
    color_palette: list[str] = Field(min_length=2, max_length=6)
    audio_direction: str = Field(min_length=3, max_length=500)
    cinematic_look: str = Field(default="", max_length=500)
    continuity_rules: list[CinematicDirection] = Field(default_factory=list, max_length=8)
    realism_constraints: list[CinematicDirection] = Field(default_factory=list, max_length=8)
    scenes: list[CreativeScene] = Field(min_length=1, max_length=20)
    production_notes: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_scene_sequence(self):
        indexes = [scene.index for scene in self.scenes]
        if indexes != list(range(1, len(self.scenes) + 1)):
            raise ValueError("Scene indexes must be sequential starting at 1.")
        if sum(scene.duration_seconds for scene in self.scenes) != self.target_duration_seconds:
            raise ValueError("Scene durations must sum to the target duration.")
        return self


class DirectorPlanResponse(BaseModel):
    plan: CreativePlan
    status: str = "awaiting_approval"
    model: str
    approval_required: bool = True
