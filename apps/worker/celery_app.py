from requirement_agent.application.ingestion.tasks import register_tasks
from requirement_agent.infrastructure.queue.celery import create_celery_app

celery_app = create_celery_app()
register_tasks(celery_app)
