from celery import Celery

celery = Celery(
    "pdf_tasks",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0"
)

celery.conf.update(
    task_track_started=True,
    result_extended=True,
)
