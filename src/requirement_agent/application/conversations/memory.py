from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from requirement_agent.infrastructure.database.models import (
    AnalysisResult,
    ConversationMessage,
    RequirementConversation,
)
from requirement_agent.shared.enums import AnalysisType


async def update_conversation_memory(
    session: AsyncSession,
    source_record_id: int,
    current_extraction: dict[str, object],
    *,
    message_limit: int,
    summary_limit: int,
) -> None:
    current = (
        await session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.source_record_id == source_record_id)
        )
    ).scalar_one_or_none()
    if current is None:
        return
    conversation = (
        await session.execute(
            select(RequirementConversation)
            .where(RequirementConversation.id == current.conversation_id)
            .with_for_update()
        )
    ).scalar_one()
    messages = list(
        (
            await session.execute(
                select(ConversationMessage)
                .options(selectinload(ConversationMessage.source_record))
                .where(ConversationMessage.conversation_id == conversation.id)
                .order_by(ConversationMessage.sequence_number.desc())
                .limit(message_limit)
            )
        ).scalars().all()
    )
    messages.reverse()

    source_ids = [message.source_record_id for message in messages]
    extraction_by_source: dict[int, dict[str, object]] = {}
    if source_ids:
        analyses = (
            await session.execute(
                select(AnalysisResult)
                .where(
                    AnalysisResult.source_record_id.in_(source_ids),
                    AnalysisResult.analysis_type == AnalysisType.EXTRACTION,
                    AnalysisResult.error_message.is_(None),
                    AnalysisResult.result_json.is_not(None),
                )
                .order_by(AnalysisResult.id.desc())
            )
        ).scalars()
        for analysis in analyses:
            if (
                analysis.source_record_id not in extraction_by_source
                and analysis.result_json is not None
            ):
                extraction_by_source[analysis.source_record_id] = analysis.result_json
    extraction_by_source[source_record_id] = current_extraction

    modules: list[str] = []
    entities: dict[str, list[str]] = {}
    recent_requirements: list[dict[str, object]] = []
    evidence_message_keys: list[str] = []
    summary_parts: list[str] = []
    for message in messages:
        extraction = extraction_by_source.get(message.source_record_id, {})
        summary = _string(extraction.get("requirement_summary"))
        description = _string(extraction.get("requirement_description"))
        if not summary:
            summary = " ".join(message.source_record.raw_text.split())[:200]
        if summary:
            summary_parts.append(summary)
        for module in _strings(extraction.get("functional_modules")):
            if module not in modules:
                modules.append(module)
        raw_entities = extraction.get("entities")
        if isinstance(raw_entities, dict):
            for key, raw_value in raw_entities.items():
                values = _strings(raw_value)
                if not values:
                    single = _string(raw_value)
                    values = [single] if single else []
                bucket = entities.setdefault(str(key), [])
                for value in values:
                    if value not in bucket:
                        bucket.append(value)
        recent_requirements.append(
            {
                "message_key": message.message_key,
                "summary": summary,
                "description": description,
            }
        )
        evidence_message_keys.append(message.message_key)

    conversation.summary = "\n".join(summary_parts)[-summary_limit:]
    conversation.business_context = {
        "modules": modules,
        "entities": entities,
        "recent_requirements": recent_requirements,
        "evidence_message_keys": evidence_message_keys,
    }
    conversation.memory_revision += 1
    conversation.memory_covered_sequence = max(
        conversation.memory_covered_sequence,
        current.sequence_number,
    )
    conversation.updated_at = datetime.now(UTC)


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
