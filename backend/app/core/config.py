from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_name: str = "AI Video Studio"
    app_env: str = "development"
    studio_api_token: str | None = None
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

    ollama_base_url: str | None = None
    ollama_model: str = "qwen3.5:9b-q4_K_M"
    ollama_timeout_seconds: int = Field(default=180, ge=5, le=1800)
    comfyui_base_url: str | None = None
    comfyui_api_key: str | None = None
    comfyui_request_timeout_seconds: int = Field(default=120, ge=5, le=1800)
    comfyui_job_timeout_seconds: int = Field(default=21600, ge=60, le=86400)
    comfyui_poll_interval_seconds: float = Field(default=2.0, ge=0.5, le=30)
    max_model_output_bytes: int = Field(default=2147483648, ge=1048576, le=21474836480)
    model_workflow_registry: str | None = None
    deepfilter_script: str | None = None
    deepfilter_model_dir: str | None = None
    deepfilter_python: str | None = None
    blender_executable: str | None = None
    speech_worker_enabled: bool = False

    def local_database_url(self) -> str:
        root = Path(self.media_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return f"sqlite+pysqlite:///{(root / 'control-plane.sqlite3').as_posix()}"


settings = Settings()
