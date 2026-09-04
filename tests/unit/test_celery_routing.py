from requirement_agent.infrastructure.queue.celery import create_celery_app
from requirement_agent.shared.config import get_settings


def test_requirement_index_tasks_use_consumed_queue() -> None:
    app = create_celery_app(get_settings())
    route = app.conf.task_routes["requirement_agent.requirements.*"]

    assert route == {"queue": "source_processing"}
