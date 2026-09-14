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

# 它做的不是把原始 PDF 直接向量化，而是把审核通过后的正式需求版本组织成“需求级文档 + 功能级文档”，批量生成向量，
# 写入 RequirementEmbedding 表，并由 PostgreSQL 自动更新 HNSW 索引。

#正式需求版本 version_id
# → 判断是否已用当前 Embedding 模型索引过
# → 读取需求、版本、有效功能项
# → 查询每个功能项来自哪些原始需求
# → 组装需求级文档和功能级文档
# → 批量生成 Embedding
# → 写入 RequirementEmbedding
# → commit
# → HNSW 索引自动纳入新向量

async def index_requirement_version(
    session: AsyncSession,
    embedding_model: EmbeddingModel,
    version_id: int,
) -> None:
    #防止重复建立索引
    #当前需求版本的嵌入模型生成的向量已经存在 RequirementEmbedding 表中，则直接返回
    #这是一种幂等设计，Celery 重试或重复投递 INDEX_VERSION_TASK 时不会重复插入向量。
    #如果后续换了新的 Embedding 模型，系统会允许重新索引，保留新模型生成的向量。
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
    #查询指定的正式需求版本，并预加载它的全部功能项
    version = await session.scalar(
        select(RequirementVersion)
        .options(selectinload(RequirementVersion.features))
        .where(RequirementVersion.id == version_id)
    )
    if version is None:
        raise RequirementNotFoundError(f"requirement version {version_id} was not found")
    #查询它所属的需求主记录
    requirement = await session.get(Requirement, version.requirement_id)
    if requirement is None:
        raise RequirementNotFoundError(
            f"requirement {version.requirement_id} was not found"
        )
    #筛选出当前版本中状态为 ACTIVE 的功能项进行向量化
    features = [
        feature
        for feature in version.features
        if feature.feature_status == FeatureStatus.ACTIVE
    ]
    #查询每个功能项的来源原始需求（源于哪个原始需求，什么渠道提交，谁提交的，原始文本是什么），并去重
    sources_by_feature = await _sources_by_feature(session, version, features)
    summary_sources = _deduplicate_sources(sources_by_feature.values())
    #两类待向量化的文档：
    # 1.需求级摘要文档  这条向量适合召回“整体相似”的历史需求。
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
    # 2.功能级文档 适合召回细粒度需求
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
    #批量调用 Embedding 模型
    vectors = await embedding_model.embed([content for _, content, _ in documents])
    for (feature, content, sources), vector in zip(documents, vectors, strict=True):
        session.add(#写入 RequirementEmbedding
            RequirementEmbedding(
                requirement_id=requirement.id,#属于哪条需求
                version_id=version.id,#属于哪条正式需求版本
                requirement_key=requirement.requirement_key,#对外需求编号，如 REQ-001
                version_number=version.version_number,#版本号
                feature_key=feature.feature_key if feature else None,#若是功能级向量，则记录对应功能编号；需求级文档为 None
                title=requirement.title,#需求标题
                module=feature.module if feature else None,#功能级向量对应的业务模块；需求级向量为 None
                status=requirement.status.value,#当前需求状态
                functional_modules=list(requirement.functional_modules),#整条需求的功能模块列表
                features=[#当前全部有效功能项，用于检索结果展示和 LLM 分析
                    {
                        "feature_key": item.feature_key,
                        "module": item.module,
                        "feature_title": item.feature_title,
                        "feature_description": item.feature_description,
                        "acceptance_criteria": list(item.acceptance_criteria),
                    }
                    for item in features
                ],
                sources=sources,#当前文档对应的来源及证据
                content=content,#实际被向量化的文本
                embedding=vector,#生成出的向量
                embedding_model=embedding_model.model_name,#使用的 Embedding 模型名称
            )
        )
    await session.commit()#提交后，HNSW 自动更新


async def _sources_by_feature(#查询每个功能项的来源原始需求（源于哪个原始需求，什么渠道提交，谁提交的，原始文本是什么）
    session: AsyncSession,
    version: RequirementVersion,
    features: list[RequirementFeature],
) -> dict[str, list[dict[str, object]]]:
    #若当前版本没有有效功能项，直接返回空字典。
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
            .where(#查询每个功能项对应的来源信息。
                #不是只查当前版本新增加的来源，而是查同一条需求下这些功能项的完整修改历史，方便后续 LLM 分析时追溯来源。
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


#去重来源信息
#需求级摘要文档会汇总所有功能项的来源。但同一个原始需求可能同时支撑多个功能项，因此需要去重：
def _deduplicate_sources(
    source_groups: Iterable[list[dict[str, object]]],
) -> list[dict[str, object]]:
    unique: dict[tuple[object, object], dict[str, object]] = {}
    for group in source_groups:
        for source in group:
            unique[(source["source_key"], source["evidence_text"])] = source
    return list(unique.values())
