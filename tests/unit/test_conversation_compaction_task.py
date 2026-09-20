from celery import Celery

from requirement_agent.application.conversations.tasks import register_conversation_tasks
from requirement_agent.application.ingestion.dispatcher import (
    ANALYZE_SOURCE_TASK,
    COMPACT_CONVERSATION_TASK,
    INDEX_VERSION_TASK,
    PARSE_SOURCE_TASK,
    CeleryTaskDispatcher,
)
from requirement_agent.application.ingestion.tasks import register_tasks
from requirement_agent.infrastructure.queue.celery import create_celery_app


def test_worker_registers_conversation_compaction_and_existing_tasks() -> None:
    app = create_celery_app()

    register_tasks(app)
    register_conversation_tasks(app)

    assert {
        PARSE_SOURCE_TASK,
        ANALYZE_SOURCE_TASK,
        INDEX_VERSION_TASK,
        COMPACT_CONVERSATION_TASK,
    } <= set(app.tasks)


def test_dispatcher_uses_registered_conversation_compaction_task_name(
    monkeypatch,
) -> None:
    app = Celery("conversation-dispatch-test")
    register_conversation_tasks(app)
    sent: list[tuple[str, list[str]]] = []

    def record_send_task(name: str, args: list[str]) -> None:
        sent.append((name, args))

    monkeypatch.setattr(app, "send_task", record_send_task)

    CeleryTaskDispatcher(app).dispatch_conversation_compaction("CONV-123")

    assert COMPACT_CONVERSATION_TASK in app.tasks
    assert sent == [(COMPACT_CONVERSATION_TASK, ["CONV-123"])]
