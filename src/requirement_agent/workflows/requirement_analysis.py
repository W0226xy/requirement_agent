import json
from functools import partial
from typing import Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from requirement_agent.ai.language import (
    source_language_instruction,
    validate_output_language,
)
from requirement_agent.ai.llm.base import ChatModel, EmbeddingModel
from requirement_agent.ai.modules import (
    load_existing_modules,
    normalize_modules,
    normalize_operation_module,
)
from requirement_agent.ai.prompts.templates import (
    CONFLICT_PROMPT_VERSION,
    CONFLICT_SYSTEM_PROMPT,
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_SYSTEM_PROMPT,
)
from requirement_agent.ai.retrieval.hybrid import (
    HybridRetriever,
    RetrievalWeights,
    SearchFilters,
)
from requirement_agent.ai.schemas.analysis import (
    ConflictAnalysis,
    RequirementConflict,
    RequirementExtraction,
)
from requirement_agent.ai.schemas.retrieval import RequirementCandidate
from requirement_agent.ai.structured import StructuredLLM
from requirement_agent.infrastructure.database.models import (
    AnalysisResult,
    AuditLog,
    FeatureLineage,
    Requirement,
    RequirementVersion,
    ReviewTask,
    SourceRecord,
)
from requirement_agent.shared.enums import (
    AnalysisType,
    AuditActionType,
    AuditEntityType,
    ConflictStatus,
    ConflictType,
    ProcessingStatus,
    ReviewStatus,
)
from requirement_agent.shared.errors import (
    CandidateScopeError,
    SourceNotFoundError,
    StructuredOutputError,
)


class AnalysisState(TypedDict, total=False):
    source_record_id: int
    source_content: str
    extraction: dict[str, object]
    candidates: list[dict[str, object]]
    conflict_analysis: dict[str, object]
    existing_modules: list[str]
    exact_duplicate_keys: list[str]


class CandidateRetriever(Protocol):
    async def search(
        self,
        query: str,
        *,
        source_record_id: int | None,
        query_modules: list[str],
        filters: SearchFilters | None = None,
    ) -> list[RequirementCandidate]:
        ...


class RequirementAnalysisWorkflow:
    def __init__(
        self,
        session: AsyncSession,
        chat_model: ChatModel,
        embedding_model: EmbeddingModel,
        *,
        max_retries: int,
        retrieval_weights: RetrievalWeights,
        candidate_limit: int,
        retriever: CandidateRetriever | None = None,
    ) -> None:
        self._session = session
        self._structured_llm = StructuredLLM(
            chat_model,
            session,
            max_retries=max_retries,
        )
        self._retriever = retriever or HybridRetriever(
            session,
            embedding_model,
            retrieval_weights,
            candidate_limit=candidate_limit,
        )
        graph = StateGraph(AnalysisState)
        graph.add_node("extract", self._extract)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("analyze", self._analyze)
        graph.add_node("finalize", self._finalize)
        graph.add_edge(START, "extract")
        graph.add_edge("extract", "retrieve")
        graph.add_edge("retrieve", "analyze")
        graph.add_edge("analyze", "finalize")
        graph.add_edge("finalize", END)
        self._graph = graph.compile()

    async def run(self, source_record_id: int) -> AnalysisState:
        source = await self._get_source(source_record_id)
        if source.processing_status == ProcessingStatus.PENDING_REVIEW:
            return {"source_record_id": source_record_id}
        content = self._source_content(source)
        result = await self._graph.ainvoke(
            AnalysisState(
                source_record_id=source_record_id,
                source_content=content,
            )
        )
        return cast(AnalysisState, result)

    async def _extract(self, state: AnalysisState) -> AnalysisState:
        source_id = state["source_record_id"]
        existing_modules = await load_existing_modules(self._session)
        input_snapshot: dict[str, object] = {
            "source_content": state["source_content"],
            "existing_modules": existing_modules,
        }
        messages = [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Schema: {json.dumps(RequirementExtraction.model_json_schema())}\n"
                    f"Existing functional modules: "
                    f"{json.dumps(existing_modules, ensure_ascii=False)}\n"
                    f"{source_language_instruction(state['source_content'])}\n"
                    f"Source:\n{state['source_content']}"
                ),
            },
        ]
        try:
            extraction = await self._structured_llm.generate(
                source_record_id=source_id,
                analysis_type=AnalysisType.EXTRACTION,
                prompt_version=EXTRACTION_PROMPT_VERSION,
                schema=RequirementExtraction,
                messages=messages,
                input_snapshot=input_snapshot,
                result_validator=partial(
                    validate_output_language,
                    state["source_content"],
                    analysis_type=AnalysisType.EXTRACTION,
                ),
            )
        except Exception:
            await self._set_status(source_id, ProcessingStatus.EXTRACTION_FAILED)
            raise
        await self._set_status(source_id, ProcessingStatus.EXTRACTED)
        extraction.functional_modules = normalize_modules(
            state["source_content"],
            extraction.functional_modules,
            existing_modules,
        )
        return {
            "extraction": extraction.model_dump(mode="json"),
            "existing_modules": existing_modules,
        }

    async def _retrieve(self, state: AnalysisState) -> AnalysisState:
        source_id = state["source_record_id"]
        extraction = RequirementExtraction.model_validate(state["extraction"])
        await self._set_status(source_id, ProcessingStatus.RETRIEVING)
        query = f"{extraction.requirement_summary}\n{extraction.requirement_description}"
        try:
            semantic_candidates = await self._retriever.search(
                query,
                source_record_id=source_id,
                query_modules=extraction.functional_modules,
            )
            exact_candidates = await self._exact_duplicate_candidates(
                source_id,
                state["source_content"],
            )
        except Exception:
            await self._set_status(source_id, ProcessingStatus.ANALYSIS_FAILED)
            raise
        candidates_by_key = {
            candidate.requirement_key: candidate for candidate in semantic_candidates
        }
        candidates_by_key.update(
            {candidate.requirement_key: candidate for candidate in exact_candidates}
        )
        candidates = list(exact_candidates)
        candidates.extend(
            candidate
            for key, candidate in candidates_by_key.items()
            if key not in {item.requirement_key for item in exact_candidates}
        )
        return {
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            "exact_duplicate_keys": [
                candidate.requirement_key for candidate in exact_candidates
            ],
        }

    async def _analyze(self, state: AnalysisState) -> AnalysisState:
        source_id = state["source_record_id"]
        extraction = RequirementExtraction.model_validate(state["extraction"])
        candidates = [
            RequirementCandidate.model_validate(candidate)
            for candidate in state["candidates"]
        ]
        await self._set_status(source_id, ProcessingStatus.ANALYZING)
        input_snapshot: dict[str, object] = {
            "extraction": extraction.model_dump(mode="json"),
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        }
        messages = [
            {"role": "system", "content": CONFLICT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Schema: {json.dumps(ConflictAnalysis.model_json_schema())}\n"
                    f"{source_language_instruction(state['source_content'])}\n"
                    f"Input: {json.dumps(input_snapshot, ensure_ascii=False)}"
                ),
            },
        ]
        try:
            analysis = await self._structured_llm.generate(
                source_record_id=source_id,
                analysis_type=AnalysisType.CONFLICT_RISK,
                prompt_version=CONFLICT_PROMPT_VERSION,
                schema=ConflictAnalysis,
                messages=messages,
                input_snapshot=input_snapshot,
                result_validator=partial(
                    validate_output_language,
                    state["source_content"],
                    analysis_type=AnalysisType.CONFLICT_RISK,
                ),
            )
            analysis = analysis.model_copy(
                update={
                    "proposed_operations": [
                        operation.model_copy(
                            update={"source_record_id": source_id}
                        )
                        for operation in analysis.proposed_operations
                    ]
                }
            )

            self._validate_candidate_scope(source_id, analysis, candidates)
            self._normalize_operation_modules(
                analysis,
                state["source_content"],
                state.get("existing_modules", []),
            )
            analysis = self._enforce_exact_duplicates(
                analysis,
                state.get("exact_duplicate_keys", []),
                candidates,
                state["source_content"],
            )
        except Exception:
            await self._set_status(source_id, ProcessingStatus.ANALYSIS_FAILED)
            raise
        return {"conflict_analysis": analysis.model_dump(mode="json")}

    async def _exact_duplicate_candidates(
        self,
        source_record_id: int,
        source_content: str,
    ) -> list[RequirementCandidate]:
        previous_source = aliased(SourceRecord)
        introduced_version = aliased(RequirementVersion)
        current_version = aliased(RequirementVersion)
        rows = (
            await self._session.execute(
                select(
                    Requirement,
                    current_version,
                    previous_source,
                    FeatureLineage,
                )
                .join(
                    current_version,
                    current_version.id == Requirement.current_version_id,
                )
                .options(selectinload(current_version.features))
                .join(
                    introduced_version,
                    introduced_version.requirement_id == Requirement.id,
                )
                .join(
                    FeatureLineage,
                    FeatureLineage.introduced_version_id == introduced_version.id,
                )
                .join(
                    previous_source,
                    previous_source.id == FeatureLineage.source_record_id,
                )
                .where(
                    previous_source.id != source_record_id,
                    func.lower(func.trim(previous_source.raw_text))
                    == source_content.strip().casefold(),
                )
                .order_by(FeatureLineage.id.desc())
            )
        ).all()
        candidates: dict[str, RequirementCandidate] = {}
        for requirement, version, source, lineage in rows:
            candidates[requirement.requirement_key] = RequirementCandidate.model_validate(
                {
                    "requirement_key": requirement.requirement_key,
                    "version_number": version.version_number,
                    "title": requirement.title,
                    "functional_modules": list(requirement.functional_modules),
                    "features": [
                        {
                            "feature_key": feature.feature_key,
                            "module": feature.module,
                            "feature_title": feature.feature_title,
                            "feature_description": feature.feature_description,
                            "acceptance_criteria": list(feature.acceptance_criteria),
                        }
                        for feature in version.features
                    ],
                    "similarity_score": 1.0,
                    "matched_text": source.raw_text,
                    "sources": [
                        {
                            "source_key": source.source_key,
                            "channel_type": source.channel_type.value,
                            "submitter_name": source.submitter_name,
                            "evidence_text": lineage.evidence_text,
                        }
                    ],
                }
            )
        return list(candidates.values())

    @staticmethod
    def _normalize_operation_modules(
        analysis: ConflictAnalysis,
        source_content: str,
        existing_modules: list[str],
    ) -> None:
        for operation in analysis.proposed_operations:
            if operation.content is not None:
                operation.content.module = normalize_operation_module(
                    source_content,
                    operation.content.module,
                    existing_modules,
                )

    @staticmethod
    def _enforce_exact_duplicates(
        analysis: ConflictAnalysis,
        exact_duplicate_keys: list[str],
        candidates: list[RequirementCandidate],
        source_content: str,
    ) -> ConflictAnalysis:
        if not exact_duplicate_keys:
            return analysis
        candidate_by_key = {
            candidate.requirement_key: candidate for candidate in candidates
        }
        descriptions_are_chinese = any(
            "\u3400" <= character <= "\u9fff" for character in source_content
        )
        conflicts = [
            RequirementConflict(
                requirement_id=key,
                type=ConflictType.DUPLICATE,
                description=(
                    "该需求与已审核的历史需求内容完全相同。"
                    if descriptions_are_chinese
                    else "This requirement is identical to an approved historical requirement."
                ),
                evidence=(
                    f"原始输入与 {candidate_by_key[key].requirement_key} 的来源文本完全一致。"
                    if descriptions_are_chinese
                    else f"The source text exactly matches {candidate_by_key[key].requirement_key}."
                ),
                confidence=1.0,
            )
            for key in exact_duplicate_keys
            if key in candidate_by_key
        ]
        return ConflictAnalysis(
            conflict_status=ConflictStatus.DUPLICATE,
            related_requirement_ids=exact_duplicate_keys,
            conflicts=conflicts,
            risks=analysis.risks,
            proposed_operations=[],
            clarification_questions=analysis.clarification_questions,
        )

    async def _finalize(self, state: AnalysisState) -> AnalysisState:
        source_id = state["source_record_id"]
        result = await self._session.execute(
            select(AnalysisResult)
            .where(
                AnalysisResult.source_record_id == source_id,
                AnalysisResult.analysis_type == AnalysisType.CONFLICT_RISK,
                AnalysisResult.error_message.is_(None),
            )
            .order_by(AnalysisResult.id.desc())
            .limit(1)
        )
        analysis_result = result.scalar_one()
        existing = await self._session.scalar(
            select(ReviewTask.id).where(
                ReviewTask.analysis_result_id == analysis_result.id
            )
        )
        if existing is None:
            review = ReviewTask(
                source_record_id=source_id,
                analysis_result_id=analysis_result.id,
                review_status=ReviewStatus.PENDING,
                extraction_snapshot=state["extraction"],
                candidate_snapshot=state["candidates"],
                analysis_snapshot=state["conflict_analysis"],
            )
            self._session.add(review)
            await self._session.flush()
            self._session.add(
                AuditLog(
                    actor_id="system",
                    action_type=AuditActionType.REVIEW_TASK_CREATED,
                    entity_type=AuditEntityType.REVIEW_TASK,
                    entity_id=str(review.id),
                    after_data={"source_record_id": source_id},
                )
            )
        source = await self._session.get(SourceRecord, source_id)
        if source is None:
            raise SourceNotFoundError(f"source record {source_id} was not found")
        source.processing_status = ProcessingStatus.PENDING_REVIEW
        await self._session.commit()
        return {}

    async def _get_source(self, source_record_id: int) -> SourceRecord:
        result = await self._session.execute(
            select(SourceRecord)
            .options(selectinload(SourceRecord.attachments))
            .where(SourceRecord.id == source_record_id)
        )
        source = result.scalar_one_or_none()
        if source is None:
            raise SourceNotFoundError(f"source record {source_record_id} was not found")
        return source

    async def _set_status(
        self,
        source_record_id: int,
        status: ProcessingStatus,
    ) -> None:
        source = await self._session.get(SourceRecord, source_record_id)
        if source is None:
            raise SourceNotFoundError(f"source record {source_record_id} was not found")
        source.processing_status = status
        await self._session.commit()

    @staticmethod
    def _source_content(source: SourceRecord) -> str:
        parts = [source.raw_text.strip()]
        for attachment in source.attachments:
            parts.extend(
                text.strip()
                for text in (attachment.parsed_text, attachment.ocr_text)
                if text and text.strip()
            )
        content = "\n\n".join(part for part in parts if part)
        if not content:
            raise StructuredOutputError("source has no text available for extraction")
        return content

    @staticmethod
    def _validate_candidate_scope(
        source_record_id: int,
        analysis: ConflictAnalysis,
        candidates: list[RequirementCandidate],
    ) -> None:
        allowed_requirements = {candidate.requirement_key for candidate in candidates}
        referenced_requirements = set(analysis.related_requirement_ids)
        referenced_requirements.update(item.requirement_id for item in analysis.conflicts)
        unknown = referenced_requirements - allowed_requirements
        if unknown:
            raise CandidateScopeError(
                f"analysis referenced candidates outside retrieval scope: {sorted(unknown)}"
            )
        if any(
            operation.source_record_id != source_record_id
            for operation in analysis.proposed_operations
        ):
            raise CandidateScopeError("proposed operation referenced another source record")
