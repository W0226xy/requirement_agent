from functools import lru_cache
from typing import Protocol

from celery import Celery

from requirement_agent.infrastructure.queue.celery import create_celery_app

#把业务代码和 Celery 解耦。
#业务层只需要说“我要解析这条需求”或“我要建立这个版本的索引”，不需要关心 Redis 队列、Celery 任务名、Worker 如何消费。

PARSE_SOURCE_TASK = "requirement_agent.sources.parse"#解析原始需求附件
ANALYZE_SOURCE_TASK = "requirement_agent.sources.analyze"#对需求执行 AI 分析
INDEX_VERSION_TASK = "requirement_agent.requirements.index_version"#审核通过后建立 RAG 向量索引
FEISHU_EVENT_TASK = "requirement_agent.connectors.feishu.process_event"#异步处理飞书事件
COMPACT_CONVERSATION_TASK = "requirement_agent.conversations.compact"#压缩会话上下文
CHAT_QUERY_TASK = "requirement_agent.conversations.process_chat_query"
#任务链路：
#用户上传 PDF / Word / 图片
# → PARSE_SOURCE_TASK
# → 解析文本或 OCR
# → ANALYZE_SOURCE_TASK
# → 需求提取、RAG 检索、冲突风险分析
# → 人工审核通过
# → INDEX_VERSION_TASK
# → 向量化正式需求并写入 pgvector


class TaskDispatcher(Protocol):#任务分发抽象接口
    def dispatch_source(self, source_record_id: int) -> None:#投递“解析原始需求”任务。
        ...

    def dispatch_analysis(self, source_record_id: int) -> None:#投递需求分析任务。
        ...

    def dispatch_version(self, version_id: int) -> None:#投递建立 RAG 向量索引任务。
        ...

    def dispatch_conversation_compaction(self, conversation_key: str) -> None:#投递会话压缩任务。
        ...

    def dispatch_chat_query(self, conversation_key: str, user_message_id: int, assistant_message_id: int, actor_id: str) -> None:
        ...


class FeishuEventDispatcher(Protocol):
    def dispatch_feishu_event(self, payload: dict[str, object]) -> None:
        ...


class CeleryTaskDispatcher:# Celery 任务分发器实现
    def __init__(self, celery_app: Celery) -> None:
        self._celery_app = celery_app#创建的 Celery 应用。它内部通常配置 Redis 作为 Broker 和结果后端。

    #投递“解析原始需求”任务
    #Celery 将任务消息写入 Redis，Worker 取到任务后执行对应的 parse_source_task
    def dispatch_source(self, source_record_id: int) -> None:
        self._celery_app.send_task(PARSE_SOURCE_TASK, args=[source_record_id])

    #投递 AI 分析任务
    #Celery 将任务消息写入 Redis，Worker 取到任务后执行对应的 analyze_source_task
    def dispatch_analysis(self, source_record_id: int) -> None:
        self._celery_app.send_task(ANALYZE_SOURCE_TASK, args=[source_record_id])

    #投递建立 RAG 向量索引任务
    #Celery 将任务消息写入 Redis，Worker 取到任务后执行对应的 index_version_task
    def dispatch_version(self, version_id: int) -> None:
        self._celery_app.send_task(INDEX_VERSION_TASK, args=[version_id])

    def dispatch_conversation_compaction(self, conversation_key: str) -> None:#投递会话压缩任务
        self._celery_app.send_task(COMPACT_CONVERSATION_TASK, args=[conversation_key])

    def dispatch_chat_query(self, conversation_key: str, user_message_id: int, assistant_message_id: int, actor_id: str) -> None:
        self._celery_app.send_task(CHAT_QUERY_TASK, args=[conversation_key, user_message_id, assistant_message_id, actor_id])

    #投递飞书事件
    #Celery 将任务消息写入 Redis，Worker 取到任务后执行对应的 feishu_event_task
    def dispatch_feishu_event(self, payload: dict[str, object]) -> None:
        self._celery_app.send_task(FEISHU_EVENT_TASK, args=[payload])


@lru_cache
def get_task_dispatcher() -> CeleryTaskDispatcher:#返回 CeleryTaskDispatcher 实例，使用 lru_cache 缓存，避免重复创建 Celery 应用。
    return CeleryTaskDispatcher(create_celery_app())
