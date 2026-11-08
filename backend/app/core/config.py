from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "AI Video Studio"
    app_env: str = "development"
    log_level: str = "INFO"
    media_root: str = "/srv/video-studio/data"
    max_upload_gb: int = 20
    max_project_gpu_seconds: int = 7200
    max_generation_retries_per_shot: int = 2
    require_approval_before_generation: bool = True
    require_approval_before_export: bool = True


settings = Settings()
