from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class CreativePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=160)
    logline: str = Field(min_length=5, max_length=500)
    target_duration_seconds: int = Field(ge=1, le=300)
    audience: str = Field(min_length=2, max_length=300)
    visual_style: str = Field(min_length=3, max_length=500)
    color_palette: list[str] = Field(min_length=2, max_length=6)
    audio_direction: str = Field(min_length=3, max_length=500)
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
