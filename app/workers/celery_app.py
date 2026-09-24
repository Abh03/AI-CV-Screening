from celery import Celery

from app.config import settings

celery_app = Celery("cv_screening", broker=settings.REDIS_URL, backend=settings.REDIS_URL,
                    include=["app.workers.tasks"])
celery_app.conf.update(
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
    }},
)
