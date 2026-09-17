"""Persistent conversation memory and asynchronous compaction."""
import json

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from requirement_agent.ai.llm.base import ChatModel
from requirement_agent.ai.prompts.templates import CONVERSATION_SUMMARY_SYSTEM_PROMPT
from requirement_agent.infrastructure.database.models import (
    AnalysisResult,
    ConversationMessage,
    RequirementConversation,
    ReviewTask,
)
from requirement_agent.shared.enums import AnalysisType


SUMMARY_SECTIONS = (
    "已确认需求",
    "待确认问题",
    "已分析来源",
    "已发现冲突或关联",
    "审核与版本状态",
    "已失效或被替代信息",
)
SUMMARY_ITEM_LIMITS = {
    "已确认需求": 8,
    "待确认问题": 5,
    "已分析来源": 8,
    "已发现冲突或关联": 5,
    "审核与版本状态": 8,
    "已失效或被替代信息": 8,
}


async def should_compact_conversation(
    session: AsyncSession, source_record_id: int, *, window_size: int,
    message_threshold: int, char_threshold: int,
) -> str | None:
    """Return a key only when old, uncovered turns need compaction."""
    row = (await session.execute(
        select(ConversationMessage, RequirementConversation).join(RequirementConversation)
        .where(ConversationMessage.source_record_id == source_record_id)
    )).one_or_none()
    if row is None:
        return None
    message, conversation = row
    old_end = message.sequence_number - window_size
    if old_end <= conversation.memory_covered_sequence:
        return None
    from requirement_agent.infrastructure.database.models import SourceRecord
    chars = await session.scalar(
        select(func.coalesce(func.sum(func.length(SourceRecord.raw_text)), 0))
        .select_from(ConversationMessage).join(SourceRecord)
        .where(ConversationMessage.conversation_id == conversation.id,
               ConversationMessage.sequence_number > conversation.memory_covered_sequence,
               ConversationMessage.sequence_number <= old_end)
    )
    total = await session.scalar(select(func.count(ConversationMessage.id)).where(
        ConversationMessage.conversation_id == conversation.id))
    if (total or 0) >= message_threshold or (chars or 0) >= char_threshold:
        return conversation.conversation_key
    return None


async def compact_conversation(
    conversation_key: str, session_factory: async_sessionmaker[AsyncSession],
    chat_model: ChatModel, *, window_size: int, summary_limit: int,
) -> bool:
    """Optimistically replace a summary; stale concurrent workers give up.

    This never deletes raw messages or any business/audit records.
    """
    async with session_factory() as session:
        conversation = (await session.execute(select(RequirementConversation).where(
            RequirementConversation.conversation_key == conversation_key,
            RequirementConversation.deleted_at.is_(None),
        ))).scalar_one_or_none()
        if conversation is None:
            return False
        revision = conversation.memory_revision
        max_sequence = await session.scalar(
            select(func.max(ConversationMessage.sequence_number)).where(
                ConversationMessage.conversation_id == conversation.id
            )
        )
        old_end = (max_sequence or 0) - window_size
        if old_end <= conversation.memory_covered_sequence:
            return False
        messages = list((await session.execute(
            select(ConversationMessage).options(selectinload(ConversationMessage.source_record))
            .where(ConversationMessage.conversation_id == conversation.id,
                   ConversationMessage.sequence_number > conversation.memory_covered_sequence,
                   ConversationMessage.sequence_number <= old_end)
            .order_by(ConversationMessage.sequence_number)
        )).scalars())
        if not messages:
            return False
        source_ids = [item.source_record_id for item in messages]
        analyses = list((await session.execute(select(AnalysisResult).where(
            AnalysisResult.source_record_id.in_(source_ids),
            AnalysisResult.analysis_type.in_([AnalysisType.EXTRACTION, AnalysisType.CONFLICT_RISK]),
            AnalysisResult.error_message.is_(None),
        ).order_by(AnalysisResult.id.desc()))).scalars())
        latest: dict[tuple[int, AnalysisType], dict[str, object]] = {}
        for item in analyses:
            if item.result_json is not None:
                latest.setdefault((item.source_record_id, item.analysis_type), item.result_json)
        reviews = list((await session.execute(select(ReviewTask).where(
            ReviewTask.source_record_id.in_(source_ids)).order_by(ReviewTask.id.desc()))).scalars())
        review_by_source: dict[int, str] = {}
        for item in reviews:
            review_by_source.setdefault(item.source_record_id, item.review_status.value)
        facts = [{
            "SourceRecord ID": item.source_record_id,
            "消息序号": item.sequence_number,
            "用户消息": item.source_record.raw_text[:2000],
            "最新需求提取": _summary_evidence(
                latest.get((item.source_record_id, AnalysisType.EXTRACTION)),
                ("requirement_summary", "requirement_description", "functional_modules",
                 "acceptance_criteria", "clarification_questions", "entities"),
            ),
            "最新冲突分析": _summary_evidence(
                latest.get((item.source_record_id, AnalysisType.CONFLICT_RISK)),
                ("conflict_status", "related_requirement_ids", "conflicts", "risks",
                 "proposed_operations", "clarification_questions"),
            ),
            "审核状态": review_by_source.get(item.source_record_id),
        } for item in messages]
        summary_input = json.dumps(
            {
                "旧状态摘要": conversation.summary,
                "本轮新增事实": facts,
                "摘要字符上限": summary_limit,
                "本轮覆盖序号": {
                    "from": conversation.memory_covered_sequence + 1,
                    "to": old_end,
                },
            },
            ensure_ascii=False,
        )
        generated_summary = (
            await chat_model.complete(
                [
                    {"role": "system", "content": CONVERSATION_SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": summary_input},
                ],
                analysis_type="conversation_summary",
            )
        )
        new_summary = _fit_summary_to_limit(generated_summary, summary_limit)
        if not new_summary:
            return False
        result = await session.execute(update(RequirementConversation).where(
            RequirementConversation.id == conversation.id,
            RequirementConversation.memory_revision == revision,
        ).values(summary=new_summary, memory_covered_sequence=old_end,
                 memory_revision=revision + 1))
        if result.rowcount != 1:
            await session.rollback()
            return False
        await session.commit()
        return True


def _summary_evidence(
    result: dict[str, object] | None, keys: tuple[str, ...],
) -> dict[str, object] | None:
    """Expose concise current evidence, not a historical tool-result dump."""
    if result is None:
        return None
    return {key: result[key] for key in keys if key in result}


def _fit_summary_to_limit(value: str, limit: int) -> str:
    """Keep complete state-summary entries within the configured character budget.

    The model is instructed to emit one bullet per fact.  This defensive layer
    preserves section headers and whole lines only; it never slices an ID, a
    fact, or a heading in the middle.
    """
    if limit <= 0:
        return ""
    section_items: dict[str, list[str]] = {section: [] for section in SUMMARY_SECTIONS}
    current: str | None = None
    for raw_line in value.strip().splitlines():
        line = raw_line.strip()
        matched = next((section for section in SUMMARY_SECTIONS if line.startswith(
            f"{section}："
        )), None)
        if matched is not None:
            current = matched
            inline_item = line.removeprefix(f"{matched}：").strip()
            if inline_item and inline_item != "无":
                section_items[current].append(inline_item)
        elif current is not None and line and line != "无":
            section_items[current].append(line)

    lines: list[str] = []
    for section in SUMMARY_SECTIONS:
        heading = f"{section}："
        if not _can_append(lines, heading, limit):
            break
        lines.append(heading)
        for item in section_items[section][:SUMMARY_ITEM_LIMITS[section]]:
            if not _can_append(lines, item, limit):
                break
            lines.append(item)
    return "\n".join(lines)


def _can_append(lines: list[str], item: str, limit: int) -> bool:
    return len("\n".join([*lines, item])) <= limit
