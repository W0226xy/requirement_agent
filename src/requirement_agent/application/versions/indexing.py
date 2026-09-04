from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from requirement_agent.ai.llm.base import EmbeddingModel
from requirement_agent.infrastructure.database.models import (
    FeatureLineage,
    Requirement,
    RequirementEmbedding,
    RequirementFeature,
    RequirementVersion,
    SourceRecord,
)
from requirement_agent.shared.enums import FeatureStatus
from requirement_agent.shared.errors import RequirementNotFoundError


async def index_requirement_version(
    session: AsyncSession,
    embedding_model: EmbeddingModel,
    version_id: int,
) -> None:
    exists = await session.scalar(
        select(RequirementEmbedding.id)
        .where(
            RequirementEmbedding.version_id == version_id,
            RequirementEmbedding.embedding_model == embedding_model.model_name,
        )
        .limit(1)
    )
    if exists is not None:
        return
    version = await session.scalar(
        select(RequirementVersion)
        .options(selectinload(RequirementVersion.features))
        .where(RequirementVersion.id == version_id)
    )
    if version is None:
        raise RequirementNotFoundError(f"requirement version {version_id} was not found")
    requirement = await session.get(Requirement, version.requirement_id)
    if requirement is None:
        raise RequirementNotFoundError(
            f"requirement {version.requirement_id} was not found"
        )
    features = [
        feature
        for feature in version.features
        if feature.feature_status == FeatureStatus.ACTIVE
    ]
    sources_by_feature = await _sources_by_feature(session, version, features)
    summary_sources = _deduplicate_sources(sources_by_feature.values())
    documents: list[tuple[RequirementFeature | None, str, list[dict[str, object]]]] = [
        (
            None,
            "\n".join(
                [
                    requirement.title,
                    *[
                        f"{item.feature_title}: {item.feature_description}"
                        for item in features
                    ],
                ]
            ),
            summary_sources,
        )
    ]
    documents.extend(
        (
            feature,
            "\n".join(
                [
                    feature.feature_title,
                    feature.feature_description,
                    *feature.acceptance_criteria,
                ]
            ),
            sources_by_feature.get(feature.feature_key, []),
        )
        for feature in features
    )
    vectors = await embedding_model.embed([content for _, content, _ in documents])
    for (feature, content, sources), vector in zip(documents, vectors, strict=True):
        session.add(
            RequirementEmbedding(
                requirement_id=requirement.id,
                version_id=version.id,
                requirement_key=requirement.requirement_key,
                version_number=version.version_number,
                feature_key=feature.feature_key if feature else None,
                title=requirement.title,
                module=feature.module if feature else None,
                status=requirement.status.value,
                functional_modules=list(requirement.functional_modules),
                features=[
                    {
                        "feature_key": item.feature_key,
                        "module": item.module,
                        "feature_title": item.feature_title,
                        "feature_description": item.feature_description,
                        "acceptance_criteria": list(item.acceptance_criteria),
                    }
                    for item in features
                ],
                sources=sources,
                content=content,
                embedding=vector,
                embedding_model=embedding_model.model_name,
            )
        )
    await session.commit()


async def _sources_by_feature(
    session: AsyncSession,
    version: RequirementVersion,
    features: list[RequirementFeature],
) -> dict[str, list[dict[str, object]]]:
    keys = [feature.feature_key for feature in features]
    if not keys:
        return {}
    rows = (
        await session.execute(
            select(FeatureLineage, SourceRecord)
            .join(
                RequirementVersion,
                RequirementVersion.id == FeatureLineage.introduced_version_id,
            )
            .join(SourceRecord, SourceRecord.id == FeatureLineage.source_record_id)
            .where(
                RequirementVersion.requirement_id == version.requirement_id,
                FeatureLineage.feature_key.in_(keys),
            )
            .order_by(FeatureLineage.created_at, FeatureLineage.id)
        )
    ).all()
    result: dict[str, list[dict[str, object]]] = {}
    for lineage, source in rows:
        result.setdefault(lineage.feature_key, []).append(
            {
                "source_key": source.source_key,
                "channel_type": source.channel_type.value,
                "submitter_name": source.submitter_name,
                "evidence_text": lineage.evidence_text,
            }
        )
    return result


def _deduplicate_sources(
    source_groups: Iterable[list[dict[str, object]]],
) -> list[dict[str, object]]:
    unique: dict[tuple[object, object], dict[str, object]] = {}
    for group in source_groups:
        for source in group:
            unique[(source["source_key"], source["evidence_text"])] = source
    return list(unique.values())
