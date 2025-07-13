from celery import Celery
import os

os.environ.setdefault("FORKED_BY_MULTIPROCESSING", "1")

celery_app = Celery(
    "my_app",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0",
)

celery_app.config_from_object('celeryconfig')

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="America/Caracas",
    enable_utc=True,
)