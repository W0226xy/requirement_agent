"""Celery task registration for asynchronous conversation-memory compaction."""
import logging

from celery import Celery
from sqlalchemy import select, update

from requirement_agent.ai.llm.factory import get_llm
from requirement_agent.application.conversations.memory import compact_conversation
from requirement_agent.application.ingestion.dispatcher import COMPACT_CONVERSATION_TASK
from requirement_agent.application.ingestion.dispatcher import CHAT_QUERY_TASK
from requirement_agent.ai.llm.factory import get_embedding_model
from requirement_agent.ai.retrieval.hybrid import HybridRetriever, RetrievalWeights
from requirement_agent.application.chat_agent import ChatAgentService
from requirement_agent.ai.skills import get_skill
from requirement_agent.application.conversations.intent import ConversationIntent, classify_conversation_message
from requirement_agent.application.chat_tools import ChatToolService
from requirement_agent.infrastructure.database.models import ConversationMessage
from requirement_agent.application.ingestion.tasks import run_worker_coroutine
from requirement_agent.infrastructure.database.models import RequirementConversation
from requirement_agent.infrastructure.database.session import get_session_factory
from requirement_agent.shared.config import get_settings

logger = logging.getLogger(__name__)

#Worker 启动时注册任务
def register_conversation_tasks(celery_app: Celery) -> None:
    """Register conversation tasks on the Worker application at startup."""
    #这里定义了 Celery 能直接执行的同步函数。
    def compact_conversation_task(conversation_key: str) -> None:
        run_worker_coroutine(compact_conversation_memory(conversation_key))
    #把异步协程放进 Worker 的事件循环中执行。
    celery_app.task(name=COMPACT_CONVERSATION_TASK)(compact_conversation_task)
    def process_chat_query_task(conversation_key: str, user_message_id: int, assistant_message_id: int, actor_id: str) -> None:
        run_worker_coroutine(process_chat_query(conversation_key, user_message_id, assistant_message_id, actor_id))
    celery_app.task(name=CHAT_QUERY_TASK)(process_chat_query_task)


async def process_chat_query(conversation_key: str, user_message_id: int, assistant_message_id: int, actor_id: str) -> bool:
    async with get_session_factory()() as session:
        claimed = await session.execute(update(ConversationMessage).where(
            ConversationMessage.id == assistant_message_id,
            ConversationMessage.reply_to_message_id == user_message_id,
            ConversationMessage.chat_status == "pending",
        ).values(chat_status="processing"))
        await session.commit()
        if claimed.rowcount != 1:
            return False
        try:
            user_message = await session.get(ConversationMessage, user_message_id)
            if user_message is None or user_message.conversation_id is None:
                raise ValueError("query message was not found")
            settings = get_settings()
            retriever = HybridRetriever(session, get_embedding_model(), RetrievalWeights(
                keyword=settings.retrieval_keyword_weight, vector=settings.retrieval_vector_weight,
                business=settings.retrieval_business_weight), candidate_limit=settings.retrieval_candidate_limit,
                min_similarity_score=settings.retrieval_min_similarity_score)
            intent = classify_conversation_message(user_message.content, has_attachment=False)
            skill = get_skill("requirement_reporting") if intent == ConversationIntent.REPORTING_QUERY else get_skill()
            answer, tool_calls, references = await ChatAgentService(
                session, get_llm(), ChatToolService(session, retriever, actor_id), actor_id, conversation_key, skill
            ).answer(user_message.content)
            await session.execute(update(ConversationMessage).where(ConversationMessage.id == assistant_message_id).values(
                chat_status="completed", content=answer, tool_calls=tool_calls, references=references
            ))
            await session.commit()
            return True
        except Exception:
            logger.exception("chat query failed conversation_key=%s assistant_message_id=%s", conversation_key, assistant_message_id)
            await session.execute(update(ConversationMessage).where(ConversationMessage.id == assistant_message_id).values(
                chat_status="failed", content="查询处理失败，请重试"
            ))
            await session.commit()
            return False

#Celery Worker 取到任务后，会把对应的 conversation_key 传进来，只压缩这一个会话，不会影响其他会话。
async def compact_conversation_memory(conversation_key: str) -> bool:
    """Compact a conversation best-effort and leave an operational audit trail."""
    logger.info("Conversation compaction started: conversation_key=%s", conversation_key)
    settings = get_settings()
    #读取配置，包括
    #window_size=conversation_context_recent_message_limit 最近保留多少条原始消息，不压缩
    #summary_limit=conversation_memory_summary_limit 生成的会话摘要最多允许多长
    try:
        compacted = await compact_conversation(
            conversation_key,#指定要压缩哪个会话
            get_session_factory(),#创建数据库会话，读取和更新会话、消息、摘要
            get_llm(),#获取 LLM 实例，用于生成会话摘要
            window_size=settings.conversation_context_recent_message_limit,#控制保留窗口和摘要长度
            summary_limit=settings.conversation_memory_summary_limit,
        )
        #压缩成功后，再查询一次数据库中最终保存的摘要长度，用于日志观察
        summary_length = await _get_summary_length(conversation_key) if compacted else 0
        if compacted:
            logger.info(
                "Conversation compaction succeeded: conversation_key=%s summary_length=%d",
                conversation_key,
                summary_length,
            )
        else:
            logger.info(
                "Conversation compaction finished without changes: "
                "conversation_key=%s summary_length=%d",
                conversation_key,
                summary_length,
            )
        return compacted
    except Exception:
        logger.exception(
            "Conversation compaction failed: conversation_key=%s summary_length=0",
            conversation_key,
        )
        return False


async def _get_summary_length(conversation_key: str) -> int:#获取指定会话的摘要长度
    async with get_session_factory()() as session:
        summary = await session.scalar(
            select(RequirementConversation.summary).where(
                RequirementConversation.conversation_key == conversation_key,
                RequirementConversation.deleted_at.is_(None),
            )
        )
    return len(summary or "")
