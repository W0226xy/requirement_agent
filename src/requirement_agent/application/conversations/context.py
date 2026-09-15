import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from requirement_agent.infrastructure.database.models import (
    AnalysisResult,
    ConversationMessage,
    RequirementConversation,
)
from requirement_agent.shared.enums import AnalysisType

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
        )
    ).one_or_none()
    # 当前需求不属于会话时，不加载会话记忆。
    if current is None:
        return ""

    #查询当前消息之前的历史消息
    current_message, conversation = current
    previous = list(
        (
            await session.execute(
                select(ConversationMessage)
                .options(selectinload(ConversationMessage.source_record))
                .where(
                    ConversationMessage.conversation_id == conversation.id,#只找同一个会话里的消息。
                    ConversationMessage.sequence_number
                    < current_message.sequence_number,
                )
                .order_by(ConversationMessage.sequence_number.desc())#只找当前消息之前的内容，不把当前消息自己加入“历史记忆”。
                .limit(message_limit)
            )
        ).scalars().all()
    )
    previous.reverse()#把历史消息按时间顺序排列，最早的消息在前，最新的消息在后。

    #先取出所有历史消息对应的 source_record_id，再批量查这些消息历史上已有的分析结果。
    source_ids = [message.source_record_id for message in previous]#
    snapshots: dict[tuple[int, AnalysisType], dict[str, object]] = {}
    if source_ids:
        analyses = (
            await session.execute(
                select(AnalysisResult)
                .where(
                    AnalysisResult.source_record_id.in_(source_ids),
                    AnalysisResult.analysis_type.in_(
                        [AnalysisType.EXTRACTION, AnalysisType.CONFLICT_RISK]
                    ),
                    AnalysisResult.error_message.is_(None),
                    AnalysisResult.result_json.is_not(None),
                )
                .order_by(AnalysisResult.id.desc())
            )
        ).scalars()
        for analysis in analyses:
            key = (analysis.source_record_id, analysis.analysis_type)
            if key not in snapshots and analysis.result_json is not None:
                snapshots[key] = analysis.result_json

    # 历史记忆只用于补充语境：原始附件全文既昂贵又不可信，优先保留已有结构化分析。
    # Summary 和 business_context 来自会话记忆；历史消息按时间正序输出。
    footer = "\n</conversation_memory>"
    opening = '<conversation_memory trust="untrusted-context-only">\n'
    recent_label = "Recent messages from this conversation only:\n"
    business_context = json.dumps(
        conversation.business_context, ensure_ascii=False, sort_keys=True
    )
    fixed_length = len(opening) + len(footer) + len("Summary: \n") + len(
        "Business context: \n"
    ) + len(recent_label)
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
        f"{recent_label}"
    )

    blocks: list[str] = []
    remaining = char_limit - len(header) - len(footer)
    #从最近消息开始装入记忆
    for message in reversed(previous):
        source = message.source_record
        payload = {
            "message_key": message.message_key,
            "sequence_number": message.sequence_number,
            "role": message.role,
            "content": source.raw_text.strip(),
            "extraction": snapshots.get(
                (message.source_record_id, AnalysisType.EXTRACTION)
            ),
            "conflict_analysis": snapshots.get(
                (message.source_record_id, AnalysisType.CONFLICT_RISK)
            ),
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
