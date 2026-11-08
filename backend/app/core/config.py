from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_name: str = "AI Video Studio"
    app_env: str = "development"
    log_level: str = "INFO"
    media_root: str = "/srv/video-studio/data"
    database_url: str | None = None
    database_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str | None = None
    max_upload_gb: int = 20
    max_project_gpu_seconds: int = 7200
    max_generation_retries_per_shot: int = 2
    require_approval_before_generation: bool = True
    require_approval_before_export: bool = True
    job_default_max_attempts: int = 3
    job_heartbeat_interval_seconds: int = 10
    job_stale_after_seconds: int = 90
    outbox_lock_timeout_seconds: int = 30
    outbox_batch_size: int = 50

    def local_database_url(self) -> str:
        root = Path(self.media_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return f"sqlite+pysqlite:///{(root / 'control-plane.sqlite3').as_posix()}"


settings = Settings()
