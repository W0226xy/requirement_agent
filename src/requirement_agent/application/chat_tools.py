"""Application services behind chat tools.  Tool modules never issue SQL directly."""
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from requirement_agent.ai.retrieval.hybrid import HybridRetriever, SearchFilters
from requirement_agent.ai.tools.schemas import (
    ConversationSummaryOutput, GetConversationSummaryInput, GetRequirementDetailInput,
    GetRequirementReportSnapshotInput, GetSourceDetailInput, RequirementDetailOutput,
    RequirementReportSnapshotOutput, RequirementSearchItem,
    RequirementVersionSummary, SearchRequirementsInput, SearchRequirementsOutput,
    SourceDetailOutput,
)
from requirement_agent.infrastructure.database.models import (
    AnalysisResult, ConversationMessage, FeatureLineage, Requirement, RequirementFeature,
    RequirementVersion, RequirementConversation, ReviewTask, SourceRecord,
)
from requirement_agent.shared.enums import AnalysisType, ReviewStatus


class ChatToolService:
    """Read-only, owner-scoped projection service for the Agent."""
    def __init__(self, session: AsyncSession, retriever: HybridRetriever, actor_id: str) -> None:
        self._session = session
        self._retriever = retriever
        self._actor_id = actor_id

    async def search_requirements(self, value: SearchRequirementsInput) -> SearchRequirementsOutput:
        candidates = await self._retriever.search_traceability(
            value.query, query_modules=value.modules,
            filters=SearchFilters(modules=tuple(value.modules), statuses=tuple(value.statuses)),
        )
        keys = [item.requirement_key for item in candidates[:value.top_k]]
        statuses = {item.requirement_key: item.status.value for item in (await self._session.execute(
            select(Requirement).where(Requirement.requirement_key.in_(keys))
        )).scalars()}
        return SearchRequirementsOutput(items=[RequirementSearchItem(
            requirement_key=item.requirement_key, version_number=item.version_number,
            title=item.title, functional_modules=item.functional_modules,
            status=statuses.get(item.requirement_key, "unknown"),
            relevance_score=round(item.similarity_score, 4),
            match_reason=f"与查询语义和关键词匹配，相关度 {item.similarity_score:.2f}",
        ) for item in candidates[:value.top_k]])

    async def get_requirement_detail(self, value: GetRequirementDetailInput) -> RequirementDetailOutput | None:
        requirement = await self._session.scalar(select(Requirement).where(
            Requirement.requirement_key == value.requirement_key
        ))
        if requirement is None:
            return None
        versions = list((await self._session.execute(select(RequirementVersion).options(
            selectinload(RequirementVersion.features)
        ).where(RequirementVersion.requirement_id == requirement.id).order_by(
            RequirementVersion.version_number.desc()
        ))).scalars())
        current = next((item for item in versions if item.id == requirement.current_version_id), None)
        features = current.features if current else []
        lineage = list((await self._session.execute(select(FeatureLineage).join(
            RequirementVersion, FeatureLineage.introduced_version_id == RequirementVersion.id
        ).where(RequirementVersion.requirement_id == requirement.id).order_by(
            FeatureLineage.created_at, FeatureLineage.id
        ))).scalars())
        snapshot = current.requirement_snapshot if current else {}
        return RequirementDetailOutput(
            requirement_key=requirement.requirement_key,
            current_version=current.version_number if current else None, title=requirement.title,
            description=str(
                snapshot.get("description")
                or snapshot.get("requirement_description")
                or "\n".join(feature.feature_description for feature in features)
            ),
            acceptance_criteria=[criterion for feature in features for criterion in feature.acceptance_criteria],
            review_status=requirement.status.value,
            history=[RequirementVersionSummary(version_number=item.version_number,
                change_type=item.change_type.value, change_reason=item.change_reason,
                created_at=item.created_at) for item in versions[:20]],
            source_record_ids=sorted({item.source_record_id for item in lineage}),
            feature_lineage=[{"feature_key": item.feature_key, "source_record_id": item.source_record_id,
                "introduced_version_id": item.introduced_version_id, "operation_type": item.operation_type.value,
                "evidence_text": item.evidence_text[:500]} for item in lineage[:100]],
        )

    async def get_source_detail(self, value: GetSourceDetailInput) -> SourceDetailOutput | None:
        source = await self._session.scalar(select(SourceRecord).options(
            selectinload(SourceRecord.attachments)
        ).where(SourceRecord.id == value.source_record_id))
        if source is None:
            return None
        analyses = list((await self._session.execute(select(AnalysisResult).where(
            AnalysisResult.source_record_id == source.id
        ).order_by(AnalysisResult.id.desc()).limit(10))).scalars())
        return SourceDetailOutput(source_record_id=source.id, source_key=source.source_key,
            channel_type=source.channel_type.value, submitted_at=source.received_at,
            raw_text_summary=_summary(source.raw_text, 1000), processing_status=source.processing_status.value,
            attachments=[{"file_name": item.file_name, "file_type": item.file_type,
                "parse_status": item.parse_status.value} for item in source.attachments],
            analyses=[{"analysis_type": item.analysis_type.value, "status": "failed" if item.error_message else "completed",
                "result_summary": _summary(str(item.result_json or ""), 500)} for item in analyses])

    async def get_conversation_summary(self, value: GetConversationSummaryInput) -> ConversationSummaryOutput | None:
        conversation = await self._session.scalar(select(RequirementConversation).where(
            RequirementConversation.conversation_key == value.conversation_key,
            RequirementConversation.owner_id == self._actor_id,
            RequirementConversation.deleted_at.is_(None),
        ))
        if conversation is None:
            return None
        recent = list((await self._session.execute(select(ConversationMessage).options(
            selectinload(ConversationMessage.source_record)
        ).where(ConversationMessage.conversation_id == conversation.id).order_by(
            ConversationMessage.sequence_number.desc()
        ).limit(5))).scalars())
        summary = conversation.summary
        return ConversationSummaryOutput(conversation_key=conversation.conversation_key,
            confirmed_requirements=_section_items(summary, "已确认需求"),
            pending_questions=_section_items(summary, "待确认问题"),
            discovered_conflicts=_section_items(summary, "已发现冲突或关联"),
            recent_messages=[{"message_key": item.message_key, "sequence_number": item.sequence_number,
                "summary": _summary(item.source_record.raw_text if item.source_record else item.content, 300)} for item in reversed(recent)])

    async def get_requirement_report_snapshot(
        self, value: GetRequirementReportSnapshotInput
    ) -> RequirementReportSnapshotOutput | None:
        """Build a bounded, owner-scoped report projection from persisted records."""
        conversation: RequirementConversation | None = None
        scope_source_ids: set[int] | None = None
        if value.conversation_key:
            conversation = await self._session.scalar(select(RequirementConversation).where(
                RequirementConversation.conversation_key == value.conversation_key,
                RequirementConversation.owner_id == self._actor_id,
                RequirementConversation.deleted_at.is_(None),
            ))
            if conversation is None:
                return None
            scope_source_ids = set((await self._session.execute(select(ConversationMessage.source_record_id).where(
                ConversationMessage.conversation_id == conversation.id,
                ConversationMessage.source_record_id.is_not(None),
            ))).scalars())

        # SourceRecord is the ownership boundary.  Requirement has no owner field, so only
        # requirements with lineage to this actor's source records can enter a report.
        owner_source_ids = set((await self._session.execute(select(SourceRecord.id).where(
            SourceRecord.submitter_id == self._actor_id
        ))).scalars())
        eligible_source_ids = owner_source_ids if scope_source_ids is None else owner_source_ids & scope_source_ids
        requirement_ids: set[int] = set()
        if eligible_source_ids:
            requirement_ids = set((await self._session.execute(select(RequirementVersion.requirement_id).join(
                FeatureLineage, FeatureLineage.introduced_version_id == RequirementVersion.id
            ).where(FeatureLineage.source_record_id.in_(eligible_source_ids)))).scalars())
        requirements = []
        if requirement_ids:
            requirements = list((await self._session.execute(select(Requirement).where(
                Requirement.id.in_(requirement_ids)
            ))).scalars())
        filtered = [item for item in requirements if self._matches_report_filter(item, value)]
        filtered.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        selected = filtered[:value.limit]
        selected_ids = {item.id for item in selected}
        all_ids = {item.id for item in filtered}
        current_versions = {}
        if selected:
            current_versions = {item.id: item for item in (await self._session.execute(select(RequirementVersion).where(
                RequirementVersion.id.in_([requirement.current_version_id for requirement in selected if requirement.current_version_id is not None])
            ))).scalars()}
        lineage_by_requirement: dict[int, list[FeatureLineage]] = {item.id: [] for item in selected}
        if selected_ids:
            rows = (await self._session.execute(select(RequirementVersion.requirement_id, FeatureLineage).join(
                FeatureLineage, FeatureLineage.introduced_version_id == RequirementVersion.id
            ).where(RequirementVersion.requirement_id.in_(selected_ids), FeatureLineage.source_record_id.in_(eligible_source_ids)))).all()
            for requirement_id, lineage in rows:
                lineage_by_requirement[requirement_id].append(lineage)
        source_for_requirement = {
            requirement_id: min(lineage.source_record_id for lineage in lineages)
            for requirement_id, lineages in lineage_by_requirement.items() if lineages
        }
        status_counts = Counter(item.status.value for item in filtered)
        module_counts = Counter(module for item in filtered for module in item.functional_modules)
        review_summary = await self._review_summary(eligible_source_ids)
        risk_summary = await self._risk_summary(eligible_source_ids)
        items = [
            {"requirement_key": item.requirement_key, "title": _summary(item.title, 200),
             "modules": item.functional_modules, "status": item.status.value,
             "version": current_versions[item.current_version_id].version_number if item.current_version_id in current_versions else None,
             "updated_at": item.updated_at, "source_record_id": source_for_requirement.get(item.id)}
            for item in selected
        ]
        recent_changes = [
            {"requirement_key": item["requirement_key"], "version": item["version"],
             "updated_at": item["updated_at"], "source_record_id": item["source_record_id"]}
            for item in items[:5]
        ]
        conversation_summary = await self.get_conversation_summary(
            GetConversationSummaryInput(conversation_key=value.conversation_key)
        ) if value.conversation_key else None
        references = [{"type": "requirement", "id": item["requirement_key"]} for item in items]
        references.extend({"type": "source", "id": str(item["source_record_id"])} for item in items if item["source_record_id"] is not None)
        return RequirementReportSnapshotOutput(
            scope=(f"当前会话 {conversation.conversation_key}" if conversation else "当前用户可访问的需求库") +
                  (f"；模块：{value.module}" if value.module else ""),
            generated_at=datetime.now(UTC),
            requirement_counts={"total": len(filtered), "by_status": dict(status_counts), "by_module": dict(module_counts)},
            review_summary=review_summary, risk_conflict_summary=risk_summary,
            recent_changes=recent_changes, requirements=items,
            conversation_summary=conversation_summary, references=references,
        )

    @staticmethod
    def _matches_report_filter(requirement: Requirement, value: GetRequirementReportSnapshotInput) -> bool:
        if value.module and value.module not in requirement.functional_modules:
            return False
        if value.statuses and requirement.status.value not in value.statuses:
            return False
        updated_at = requirement.updated_at
        # SQLite fixtures can return naive timestamps whereas production PostgreSQL returns
        # timezone-aware timestamps.  Compare them in UTC without changing stored values.
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        date_from = value.date_from.astimezone(UTC) if value.date_from and value.date_from.tzinfo else value.date_from
        date_to = value.date_to.astimezone(UTC) if value.date_to and value.date_to.tzinfo else value.date_to
        if date_from and updated_at < date_from:
            return False
        return not date_to or updated_at <= date_to

    async def _review_summary(self, source_ids: set[int]) -> dict[str, int]:
        if not source_ids:
            return {"pending_count": 0, "approved_count": 0}
        reviews = list((await self._session.execute(select(ReviewTask).where(
            ReviewTask.source_record_id.in_(source_ids)
        ).order_by(ReviewTask.id.desc()))).scalars())
        latest: dict[int, ReviewTask] = {}
        for review in reviews:
            latest.setdefault(review.source_record_id, review)
        return {"pending_count": sum(item.review_status == ReviewStatus.PENDING for item in latest.values()),
                "approved_count": sum(item.review_status == ReviewStatus.APPROVED for item in latest.values())}

    async def _risk_summary(self, source_ids: set[int]) -> dict[str, object]:
        if not source_ids:
            return {"risk_counts": {}, "conflict_count": 0, "representative_items": []}
        analyses = list((await self._session.execute(select(AnalysisResult).where(
            AnalysisResult.source_record_id.in_(source_ids), AnalysisResult.analysis_type == AnalysisType.CONFLICT_RISK,
            AnalysisResult.error_message.is_(None), AnalysisResult.result_json.is_not(None),
        ).order_by(AnalysisResult.id.desc()))).scalars())
        latest: dict[int, AnalysisResult] = {}
        for analysis in analyses:
            latest.setdefault(analysis.source_record_id, analysis)
        risks: Counter[str] = Counter()
        representative: list[dict[str, object]] = []
        conflicts = 0
        for source_id, analysis in latest.items():
            payload = analysis.result_json or {}
            conflict_status = str(payload.get("conflict_status", "none"))
            if conflict_status not in ("none", "insufficient_info"):
                conflicts += 1
            for risk in payload.get("risks", []) if isinstance(payload.get("risks", []), list) else []:
                level = str(risk.get("level", "unspecified")) if isinstance(risk, dict) else "unspecified"
                risks[level] += 1
                if len(representative) < 5:
                    representative.append({"type": "risk", "source_record_id": source_id, "level": level,
                                           "summary": _summary(str(risk.get("description", risk.get("risk", "已记录风险"))) if isinstance(risk, dict) else str(risk), 240)})
            if conflict_status not in ("none", "insufficient_info") and len(representative) < 5:
                representative.append({"type": "conflict", "source_record_id": source_id,
                                       "status": conflict_status, "summary": "已记录冲突或关联"})
        return {"risk_counts": dict(risks), "conflict_count": conflicts, "representative_items": representative}


def _summary(value: str, limit: int) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else f"{value[:limit - 1]}…"


def _section_items(summary: str, heading: str) -> list[str]:
    result: list[str] = []
    active = False
    for line in summary.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{heading}："):
            active = True
            inline = stripped.removeprefix(f"{heading}：").strip()
            if inline and inline != "无": result.append(inline)
        elif active and "：" in stripped and not stripped.startswith(("-", "*")):
            break
        elif active and stripped and stripped != "无": result.append(stripped)
    return result[:8]
