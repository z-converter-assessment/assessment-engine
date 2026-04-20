from celery import Celery
from app.config import settings

celery_app = Celery(
    "assessment",
    broker=settings.celery_broker_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_routes={"app.workers.tasks.*": {"queue": "default"}},
)