from dataclasses import dataclass
from time import perf_counter

from sqlalchemy import Float, case, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from requirement_agent.ai.llm.base import EmbeddingModel
from requirement_agent.ai.schemas.retrieval import RequirementCandidate
from requirement_agent.infrastructure.database.models import (
    AnalysisResult,
    Requirement,
    RequirementEmbedding,
)
from requirement_agent.shared.enums import AnalysisType


@dataclass(frozen=True)
class RetrievalWeights:
    keyword: float
    vector: float
    business: float

    def __post_init__(self) -> None:
        if abs(self.keyword + self.vector + self.business - 1.0) > 1e-9:
            raise ValueError("retrieval weights must sum to 1.0")


@dataclass(frozen=True)
class SearchFilters:
    modules: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScoredDocument:
    requirement_key: str
    version_number: int
    title: str
    functional_modules: list[str]
    features: list[dict[str, object]]
    sources: list[dict[str, object]]
    content: str
    keyword_score: float
    vector_score: float
    business_score: float


def fuse_score(document: ScoredDocument, weights: RetrievalWeights) -> float:
    score = (
        weights.keyword * document.keyword_score
        + weights.vector * document.vector_score
        + weights.business * document.business_score
    )
    return min(1.0, max(0.0, score))


def rank_documents(
    documents: list[ScoredDocument],
    weights: RetrievalWeights,
    limit: int,
) -> list[RequirementCandidate]:
    deduplicated: dict[tuple[str, int], tuple[ScoredDocument, float]] = {}
    for document in documents:
        key = (document.requirement_key, document.version_number)
        score = fuse_score(document, weights)
        previous = deduplicated.get(key)
        if previous is None or score > previous[1]:
            deduplicated[key] = (document, score)

    ranked = sorted(deduplicated.values(), key=lambda item: item[1], reverse=True)
    return [
        RequirementCandidate.model_validate(
            {
                "requirement_key": document.requirement_key,
                "version_number": document.version_number,
                "title": document.title,
                "functional_modules": document.functional_modules,
                "features": document.features,
                "similarity_score": score,
                "matched_text": document.content[:1_000],
                "sources": document.sources,
            }
        )
        for document, score in ranked[:limit]
    ]


class HybridRetriever:
    def __init__(
        self,
        session: AsyncSession,
        embedding_model: EmbeddingModel,
        weights: RetrievalWeights,
        *,
        candidate_limit: int,
    ) -> None:
        self._session = session
        self._embedding_model = embedding_model
        self._weights = weights
        self._candidate_limit = candidate_limit

    async def search(
        self,
        query: str,
        *,
        source_record_id: int | None,
        query_modules: list[str],
        filters: SearchFilters | None = None,
    ) -> list[RequirementCandidate]:
        started = perf_counter()
        try:
            query_embedding = (await self._embedding_model.embed([query]))[0]
        except Exception as exc:
            if source_record_id is not None:
                await self._record_embedding(
                    source_record_id,
                    query,
                    None,
                    started,
                    f"{type(exc).__name__}: {exc}",
                )
            raise
        if source_record_id is not None:
            await self._record_embedding(
                source_record_id,
                query,
                query_embedding,
                started,
                None,
            )
        documents = await self._query_documents(
            query,
            query_embedding,
            query_modules,
            filters or SearchFilters(),
        )
        return rank_documents(documents, self._weights, self._candidate_limit)

    async def _query_documents(
        self,
        query: str,
        query_embedding: list[float],
        query_modules: list[str],
        filters: SearchFilters,
    ) -> list[ScoredDocument]:
        search_text = RequirementEmbedding.title + literal(" ") + RequirementEmbedding.content
        search_vector = func.to_tsvector("simple", search_text)
        search_query = func.plainto_tsquery("simple", query)
        keyword_score = cast(func.ts_rank_cd(search_vector, search_query), Float)
        vector_score = cast(
            1 - RequirementEmbedding.embedding.cosine_distance(query_embedding),
            Float,
        )
        business_score = case(
            (RequirementEmbedding.module.in_(query_modules), 1.0),
            else_=0.0,
        )
        clauses: list[ColumnElement[bool]] = []
        if filters.statuses:
            clauses.append(RequirementEmbedding.status.in_(filters.statuses))
        if filters.modules:
            clauses.append(
                or_(
                    RequirementEmbedding.module.in_(filters.modules),
                    *[
                        RequirementEmbedding.functional_modules.contains([module])
                        for module in filters.modules
                    ],
                )
            )
        statement = (
            select(
                RequirementEmbedding,
                keyword_score.label("keyword_score"),
                vector_score.label("vector_score"),
                business_score.label("business_score"),
            )
            .join(
                Requirement,
                Requirement.id == RequirementEmbedding.requirement_id,
            )
            .where(Requirement.current_version_id == RequirementEmbedding.version_id)
            .where(*clauses)
            .order_by(
                (
                    self._weights.keyword * keyword_score
                    + self._weights.vector * vector_score
                    + self._weights.business * business_score
                ).desc()
            )
            .limit(self._candidate_limit * 3)
        )
        rows = (await self._session.execute(statement)).all()
        return [
            ScoredDocument(
                requirement_key=row.RequirementEmbedding.requirement_key,
                version_number=row.RequirementEmbedding.version_number,
                title=row.RequirementEmbedding.title,
                functional_modules=list(row.RequirementEmbedding.functional_modules),
                features=list(row.RequirementEmbedding.features),
                sources=list(row.RequirementEmbedding.sources),
                content=row.RequirementEmbedding.content,
                keyword_score=min(1.0, max(0.0, float(row.keyword_score or 0))),
                vector_score=min(1.0, max(0.0, float(row.vector_score or 0))),
                business_score=float(row.business_score or 0),
            )
            for row in rows
        ]

    async def _record_embedding(
        self,
        source_record_id: int,
        query: str,
        embedding: list[float] | None,
        started: float,
        error_message: str | None,
    ) -> None:
        self._session.add(
            AnalysisResult(
                source_record_id=source_record_id,
                analysis_type=AnalysisType.EMBEDDING,
                model_name=self._embedding_model.model_name,
                prompt_version="embedding-v1",
                input_snapshot={"texts": [query]},
                result_json=(
                    {"vector_count": 1, "dimension": len(embedding)}
                    if embedding is not None
                    else None
                ),
                raw_output=None,
                confidence=None,
                duration_ms=max(0, round((perf_counter() - started) * 1000)),
                error_message=error_message,
                attempt_number=1,
            )
        )
        await self._session.commit()
