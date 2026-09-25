from celery import Celery
from celery.signals import after_setup_logger, after_setup_task_logger

from app.config import settings
from app.core.logging import SafeJSONFormatter, setup_logging
settings.validate_production()
setup_logging()


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
    }},
)
