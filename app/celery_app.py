import os
from celery import Celery

redis_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

celery_app = Celery(
    "movies_backend",
    broker=redis_url,
    backend=result_backend,
    include=["app.tasks"],  # Include tasks module
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,  # Track when tasks start
    task_time_limit=30 * 60,  # 30 minutes hard time limit
    task_soft_time_limit=25 * 60,  # 25 minutes soft time limit
    worker_prefetch_multiplier=1,  # Disable prefetching for better task distribution
)


