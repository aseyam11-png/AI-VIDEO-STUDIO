import os

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "ai_video_studio",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
)


@celery_app.task(name="studio.health_check")
def health_check() -> dict:
    return {"status": "ok", "app": settings.app_name}
