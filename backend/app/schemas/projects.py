from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    target_duration_seconds: int = Field(ge=1, le=300)
    target_resolution: str = "1920x1080"
    target_fps: int = Field(default=30, ge=1, le=120)
    language: str = "ar"
    brief: str = Field(min_length=1, max_length=12000)


class ProjectView(ProjectCreate):
    id: UUID = Field(default_factory=uuid4)
    status: str = "draft"
