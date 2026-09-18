import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from requirement_agent.infrastructure.database.models import (
    ConversationMessage,
    RequirementConversation,
    SourceRecord,
)

#当系统正在分析某条新消息时，找出它所属会话中前面的消息，
#并连同这些消息之前的提取结果、冲突分析结果，一起拼成一段上下文文本，后续传给 Agent/LLM。

async def load_conversation_context(
    session: AsyncSession,#异步数据库连接
    source_record_id: int,#当前正在分析的原始需求 ID
    *,
    message_limit: int,#最多取多少条历史消息，例如最近 5 条。
    char_limit: int,#最终拼出的记忆文本最大字符数
) -> str:
    # 先找到当前消息属于哪个会话
    current = (
        await session.execute(
            #RequirementConversation：一个完整的会话
            #ConversationMessage：一个完整会话中的一条会话消息；
            select(ConversationMessage, RequirementConversation)
            .join(
                RequirementConversation,
                RequirementConversation.id == ConversationMessage.conversation_id,
            )
            .where(ConversationMessage.source_record_id == source_record_id)
            .options(
                selectinload(ConversationMessage.source_record).selectinload(
                    SourceRecord.attachments
                )
            )
        )
    ).one_or_none()
    # 当前需求不属于会话时，不加载会话记忆。
    if current is None:
        return ""

    #查询当前消息之前的历史消息
    current_message, conversation = current
    # A covered message is represented by ``summary``.  It must never also be
    # injected as a recent turn, otherwise every compaction grows the prompt.
    previous = list(
        (
            await session.execute(
                select(ConversationMessage)
                .options(
                    selectinload(ConversationMessage.source_record).selectinload(
                        SourceRecord.attachments
                    )
                )
                .where(
                    ConversationMessage.conversation_id == conversation.id,#只找同一个会话里的消息。
                    ConversationMessage.sequence_number
                    < current_message.sequence_number,
                    ConversationMessage.sequence_number
                    > conversation.memory_covered_sequence,
                )
                .order_by(ConversationMessage.sequence_number.desc())#只找当前消息之前的内容，不把当前消息自己加入“历史记忆”。
                .limit(message_limit)
            )
        ).scalars().all()
    )
    previous.reverse()#把历史消息按时间顺序排列，最早的消息在前，最新的消息在后。

    # Historical attachment bodies, OCR, retrieval candidates and raw LLM JSON
    # are deliberately excluded.  Their durable facts belong in the summary.
    footer = "\n</conversation_memory>"
    opening = '<conversation_memory trust="untrusted-context-only">\n'
    recent_label = "最近消息（仅当前会话）：\n"
    business_context = json.dumps(
        conversation.business_context, ensure_ascii=False, sort_keys=True
    )
    current_attachments = json.dumps(
        [
            {"file_name": item.file_name, "source_record_id": source_record_id}
            for item in current_message.source_record.attachments
        ],
        ensure_ascii=False,
    )
    fixed_length = len(opening) + len(footer) + len("Summary: \n") + len(
        "Business context: \n"
    ) + len("Current attachment summary: \n") + len(current_attachments) + len(recent_label)
    if fixed_length > char_limit:
        # 极小的配置无法容纳完整 XML 包装；返回不超过上限的最小安全上下文。
        return (opening + footer)[:char_limit]

    available = char_limit - fixed_length
    if len(business_context) > available:
        # 不截断 JSON；放不下时使用完整且合法的空对象。
        business_context = "{}" if available >= 2 else ""
    summary = _truncate_text(
        conversation.summary, max(0, available - len(business_context))
    )
    header = (
        f"{opening}Summary: {summary}\n"
        f"Business context: {business_context}\n"
        f"Current attachment summary: {current_attachments}\n"
        f"{recent_label}"
    )

    blocks: list[str] = []
    remaining = char_limit - len(header) - len(footer)
    #从最近消息开始装入记忆
    for message in reversed(previous):
        source = message.source_record
        payload: dict[str, object] = {
            "message_key": message.message_key,
            "source_record_id": message.source_record_id,
            "sequence_number": message.sequence_number,
            "role": message.role,
            "content": (source.raw_text if source else message.content).strip(),
            "attachments": [
                {"file_name": attachment.file_name, "source_record_id": message.source_record_id}
                for attachment in (source.attachments if source else [])
            ],
        }
        block = _serialize_payload_within_limit(payload, remaining)
        if not block:
            # Do not spend the newest-message budget on older history.
            break
        blocks.append(block)
        remaining -= len(block) + 1
        if remaining <= 0:
            break
    blocks.reverse()
    return f"{header}{chr(10).join(blocks)}{footer}"


def _truncate_text(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    if limit == 1:
        return "…"
    return f"{value[: limit - 1]}…"


def _serialize_payload_within_limit(
    payload: dict[str, object], limit: int
) -> str | None:
    """Return a complete JSON object that fits, or omit this historical block."""
    if limit <= 0:
        return None
    candidate = dict(payload)
    serialized = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
    if len(serialized) <= limit:
        return serialized

    # Crop the specific unstructured field, then serialize again.  Never slice JSON.
    content = str(candidate["content"])
    while content:
        content = _truncate_text(content, max(0, len(content) // 2))
        candidate["content"] = content
        serialized = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
        if len(serialized) <= limit:
            return serialized

    # Structured snapshots are useful but optional for a block that cannot fit.
    candidate["extraction"] = None
    candidate["conflict_analysis"] = None
    serialized = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
    return serialized if len(serialized) <= limit else None
