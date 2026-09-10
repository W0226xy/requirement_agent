from celery import Celery

from requirement_agent.shared.config import Settings, get_settings


def create_celery_app(settings: Settings | None = None) -> Celery:
    app_settings = settings or get_settings()
    application = Celery(
        "requirement_agent",
        broker=app_settings.celery_broker_url,
        backend=app_settings.celery_result_backend,
    )
    application.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_reject_on_worker_lost=True,
        task_routes={
            "requirement_agent.sources.*": {"queue": "source_processing"},
            "requirement_agent.requirements.*": {"queue": "source_processing"},
            "requirement_agent.connectors.feishu.*": {"queue": "source_processing"},
        },
    )
    return application
