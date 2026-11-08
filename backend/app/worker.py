from datetime import datetime, timedelta, timezone

from celery import Celery
from sqlalchemy import and_, or_, select

from app.core.config import settings
from app.db.models import OutboxRecord
from app.db.session import session_scope
from app.services.job_runner import recover_stale_jobs, run_job_attempt

celery_app = Celery(
    "ai_video_studio",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 10800},
    worker_max_tasks_per_child=50,
    task_soft_time_limit=7500,
    task_time_limit=7800,
    task_routes={
        "studio.execute_job": {"queue": "cpu"},
        "studio.dispatch_outbox": {"queue": "control"},
        "studio.recover_stale_jobs": {"queue": "control"},
    },
    beat_schedule={
        "dispatch-database-outbox": {
            "task": "studio.dispatch_outbox",
            "schedule": 2.0,
        },
        "recover-stale-jobs": {
            "task": "studio.recover_stale_jobs",
            "schedule": 30.0,
        },
    },
)


def dispatch_outbox_batch(publisher=None, *, limit: int | None = None) -> int:
    """Publish committed outbox rows; duplicate deliveries are harmless by generation checks."""
    publish = publisher or celery_app.send_task
    now = datetime.now(timezone.utc)
    lock_cutoff = now - timedelta(seconds=settings.outbox_lock_timeout_seconds)
    batch_size = limit or settings.outbox_batch_size

    with session_scope() as db:
        rows = db.scalars(
            select(OutboxRecord)
            .where(
                or_(
                    and_(OutboxRecord.status == "pending", OutboxRecord.available_at <= now),
                    and_(
                        OutboxRecord.status == "dispatching",
                        or_(
                            OutboxRecord.locked_at.is_(None),
                            OutboxRecord.locked_at <= lock_cutoff,
                        ),
                    ),
                )
            )
            .order_by(OutboxRecord.created_at, OutboxRecord.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
        messages = []
        for row in rows:
            row.status = "dispatching"
            row.locked_at = now
            row.attempts += 1
            messages.append((row.id, row.job_id, row.dispatch_generation, row.queue_name))

    published = 0
    for outbox_id, job_id, generation, queue_name in messages:
        try:
            publish(
                "studio.execute_job",
                args=[job_id, generation],
                queue=queue_name,
            )
            with session_scope() as db:
                row = db.get(OutboxRecord, outbox_id)
                if row is not None:
                    row.status = "published"
                    row.published_at = datetime.now(timezone.utc)
                    row.locked_at = None
                    row.last_error = None
            published += 1
        except Exception as exc:
            with session_scope() as db:
                row = db.get(OutboxRecord, outbox_id)
                if row is not None:
                    row.status = "pending"
                    row.locked_at = None
                    backoff_seconds = min(300, 5 * (2 ** min(row.attempts, 6)))
                    row.available_at = datetime.now(timezone.utc) + timedelta(
                        seconds=backoff_seconds
                    )
                    row.last_error = str(exc)[:1000]
    return published


@celery_app.task(
    name="studio.execute_job",
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_job(job_id: str, dispatch_generation: int) -> dict:
    return run_job_attempt(job_id, dispatch_generation)


@celery_app.task(name="studio.dispatch_outbox")
def dispatch_outbox() -> dict:
    return {"published": dispatch_outbox_batch()}


@celery_app.task(name="studio.recover_stale_jobs")
def recover_stale_jobs_task() -> dict:
    return {"recovered": recover_stale_jobs()}


@celery_app.task(name="studio.health_check")
def health_check() -> dict:
    return {"status": "ok", "app": settings.app_name}
