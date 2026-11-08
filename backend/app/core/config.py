from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_name: str = "AI Video Studio"
    app_env: str = "development"
    log_level: str = "INFO"
    media_root: str = "/srv/video-studio/data"
    database_url: str | None = None
    database_echo: bool = False
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str | None = None
    max_upload_gb: int = Field(default=20, ge=1, le=200)
    max_project_gpu_seconds: int = 7200
    max_generation_retries_per_shot: int = 2
    require_approval_before_generation: bool = True
    require_approval_before_export: bool = True
    job_default_max_attempts: int = Field(default=3, ge=1, le=10)
    job_heartbeat_interval_seconds: int = Field(default=10, ge=3, le=60)
    job_stale_after_seconds: int = Field(default=90, ge=15, le=3600)
    outbox_lock_timeout_seconds: int = Field(default=30, ge=5, le=600)
    outbox_batch_size: int = Field(default=50, ge=1, le=500)

    def local_database_url(self) -> str:
        root = Path(self.media_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return f"sqlite+pysqlite:///{(root / 'control-plane.sqlite3').as_posix()}"


settings = Settings()
