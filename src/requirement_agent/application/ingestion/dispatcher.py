from functools import lru_cache
from typing import Protocol

from celery import Celery

from requirement_agent.infrastructure.queue.celery import create_celery_app

PARSE_SOURCE_TASK = "requirement_agent.sources.parse"
ANALYZE_SOURCE_TASK = "requirement_agent.sources.analyze"
INDEX_VERSION_TASK = "requirement_agent.requirements.index_version"
FEISHU_EVENT_TASK = "requirement_agent.connectors.feishu.process_event"


class TaskDispatcher(Protocol):
    def dispatch_source(self, source_record_id: int) -> None:
        ...

    def dispatch_analysis(self, source_record_id: int) -> None:
        ...

    def dispatch_version(self, version_id: int) -> None:
        ...


class FeishuEventDispatcher(Protocol):
    def dispatch_feishu_event(self, payload: dict[str, object]) -> None:
        ...


class CeleryTaskDispatcher:
    def __init__(self, celery_app: Celery) -> None:
        self._celery_app = celery_app

    def dispatch_source(self, source_record_id: int) -> None:
        self._celery_app.send_task(PARSE_SOURCE_TASK, args=[source_record_id])

    def dispatch_analysis(self, source_record_id: int) -> None:
        self._celery_app.send_task(ANALYZE_SOURCE_TASK, args=[source_record_id])

    def dispatch_version(self, version_id: int) -> None:
        self._celery_app.send_task(INDEX_VERSION_TASK, args=[version_id])

    def dispatch_feishu_event(self, payload: dict[str, object]) -> None:
        self._celery_app.send_task(FEISHU_EVENT_TASK, args=[payload])


@lru_cache
def get_task_dispatcher() -> CeleryTaskDispatcher:
    return CeleryTaskDispatcher(create_celery_app())
