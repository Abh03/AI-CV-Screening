from celery import Celery
import logging
import time
from celery.signals import (after_setup_logger, after_setup_task_logger,
                            before_task_publish, task_prerun, task_postrun)

from app.config import settings
from app.core.logging import SafeJSONFormatter, setup_logging
settings.validate_production()
setup_logging()


@before_task_publish.connect
def stamp_publication(headers=None, **_kwargs):
    if headers is not None:
        headers["campaign_published_at"] = time.time()


@task_prerun.connect
def measure_queue_wait(task_id=None, task=None, **_kwargs):
    if task is None:
        return
    task.request.capacity_started = time.monotonic()
    published = (task.request.headers or {}).get("campaign_published_at")
    if isinstance(published, (int, float)) and not isinstance(published, bool):
        logging.getLogger("cv_screening").info("Task started", extra={
            "event": "task_started", "run_id": task_id, "stage": task.name,
            "queue_wait_ms": round(max(0, time.time() - published) * 1000, 3)})


@task_postrun.connect
def measure_task_runtime(task_id=None, task=None, state=None, **_kwargs):
    started = getattr(task.request, "capacity_started", None) if task else None
    if started is not None:
        logging.getLogger("cv_screening").info("Task finished", extra={
            "event": "task_finished", "run_id": task_id, "stage": task.name,
            "status": state, "task_runtime_ms": round((time.monotonic() - started) * 1000, 3)})


@after_setup_logger.connect
@after_setup_task_logger.connect
def protect_worker_logs(logger=None, **_kwargs):
    if logger:
        for handler in logger.handlers:
            handler.setFormatter(SafeJSONFormatter())

celery_app = Celery("cv_screening", broker=settings.REDIS_URL, backend=settings.REDIS_URL,
                    include=["app.workers.tasks"])
celery_app.conf.update(
    task_routes={
        "screening.execute_run": {"queue": "screening"},
        "screening.recover_runs": {"queue": "control"},
        "campaign.stage0": {"queue": "ocr"},
        "campaign.recover_stage0": {"queue": "control"},
        "campaign.stage2_pair": {"queue": "retrieval"},
        "campaign.stage3_pair": {"queue": "evaluation"},
        "campaign.coordinate": {"queue": "control"},
        "jd.expire_drafts": {"queue": "control"},
    },
    worker_hijack_root_logger=False,
    task_serializer="json", accept_content=["json"], result_serializer="json",
    task_acks_late=True, task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1, task_track_started=True,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=2,
    task_publish_retry=False,
    broker_transport_options={"visibility_timeout": settings.CELERY_VISIBILITY_TIMEOUT_SECONDS,
                              "socket_connect_timeout": 2, "socket_timeout": 2},
    result_backend_transport_options={"visibility_timeout": settings.CELERY_VISIBILITY_TIMEOUT_SECONDS},
    task_time_limit=settings.RUN_TIMEOUT_SECONDS + 30,
    beat_schedule={"recover-screening-runs": {
        "task": "screening.recover_runs", "schedule": 60.0,
    }, "recover-campaign-stage0": {
        "task": "campaign.recover_stage0", "schedule": 60.0,
    }, "coordinate-campaigns": {
        "task": "campaign.coordinate", "schedule": 30.0,
    }, "expire-jd-drafts": {
        "task": "jd.expire_drafts", "schedule": 3600.0,
    }},
)
