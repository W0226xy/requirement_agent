import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from requirement_agent.infrastructure.database.models import (
    AnalysisResult,
    ConversationMessage,
    RequirementConversation,
    SourceRecord,
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
    if current is None:#如果当前需求不属于任何会话，就不加载会话记忆，返回空字符串。这样普通的独立需求不会被硬塞历史上下文。
        return ""

    #查询当前消息之前的历史消息
    current_message, conversation = current
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

    #拼接上下文文本，包含会话摘要、业务上下文、历史消息及其分析结果。
    header = (
        '<conversation_memory trust="untrusted-context-only">\n'
        f"Summary: {conversation.summary}\n"
        "Business context: "
        f"{json.dumps(conversation.business_context, ensure_ascii=False, sort_keys=True)}\n"
        "Previous messages from this conversation only:\n"
    )
    #限制总长度,防止后续超出模型上下文预算.如果连头尾都超过长度限制，就直接截断返回
    footer = "\n</conversation_memory>"
    if len(header) + len(footer) >= char_limit:
        return (header + footer)[:char_limit]

    blocks: list[str] = []
    remaining = char_limit - len(header) - len(footer)
    #从最近消息开始装入记忆
    for message in reversed(previous):
        source = message.source_record
        #拼接消息内容，包含原始文本、附件解析文本和附件 OCR 文本
        content_parts = [source.raw_text.strip()]
        for attachment in source.attachments:
            content_parts.extend(
                text.strip()
                for text in (attachment.parsed_text, attachment.ocr_text)
                if text and text.strip()
            )
        payload = {
            "message_key": message.message_key,
            "sequence_number": message.sequence_number,
            "role": message.role,
            "content": "\n\n".join(part for part in content_parts if part),
            "extraction": snapshots.get(
                (message.source_record_id, AnalysisType.EXTRACTION)
            ),
            "conflict_analysis": snapshots.get(
                (message.source_record_id, AnalysisType.CONFLICT_RISK)
            ),
        }
        block = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(block) + 1 > remaining:
            block = block[:remaining]
        if not block:
            break
        blocks.append(block)
        remaining -= len(block) + 1
        if remaining <= 0:
            break
    blocks.reverse()
    return f"{header}{chr(10).join(blocks)}{footer}"[:char_limit]
