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
#这段代码实现的是项目中的 混合检索器 HybridRetriever。它属于 RAG 的“检索”部分：
#Retrieval-Augmented Generation（RAG）是一种结合了检索和生成的技术，用于增强语言模型的能力。
#RAG 的核心思想是：在生成回答之前，先从外部知识库中检索相关信息，然后将这些信息作为上下文提供给语言模型，从而提高回答的准确性和丰富性。
#输入一条新需求，先生成它的向量，再从数据库中找出最可能相关的正式需求版本，最后按综合得分排序，返回给后续的冲突/风险分析 Agent。

#混合检索：关键词匹配 + 向量语义相似度 + 业务模块匹配

@dataclass(frozen=True)
class RetrievalWeights:# 检索权重，用于混合检索中各个维度的得分加权
    keyword: float#关键词匹配分数
    vector: float#向量语义相似度分数
    business: float#业务模块匹配分数

    def __post_init__(self) -> None:# 检查权重是否总和为 1.0
        if abs(self.keyword + self.vector + self.business - 1.0) > 1e-9:
            raise ValueError("retrieval weights must sum to 1.0")


@dataclass(frozen=True)
class SearchFilters:# 可选过滤条件，用于限制检索结果的业务模块和状态，例如可以在检索时只考虑某些模块或状态的需求
    modules: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
#比如，如果只想检索“支付模块”下的“已发布”需求，可以设置 modules=("支付模块",) 和 statuses=("已发布",)。

@dataclass(frozen=True)
class ScoredDocument:# 得分文档，用于存储每个检索结果的各个维度得分
    requirement_key: str#需求唯一标识
    version_number: int#需求版本号
    title: str#需求标题
    functional_modules: list[str]#需求所属的功能模块列表
    features: list[dict[str, object]]#需求的特性列表，每个特性是一个字典，包含特性名称、描述等信息
    sources: list[dict[str, object]]#需求来源列表，每个来源是一个字典，包含来源类型、来源编号等信息
    content: str#需求内容
    keyword_score: float#关键词匹配得分
    vector_score: float#向量语义相似度得分
    business_score: float#业务模块匹配得分

# 融合得分函数，用于将各个维度的得分按权重加权，得到综合得分
def fuse_score(document: ScoredDocument, weights: RetrievalWeights) -> float:
    score = (
        weights.keyword * document.keyword_score
        + weights.vector * document.vector_score
        + weights.business * document.business_score
    )
    return min(1.0, max(0.0, score))

# 排名文档函数，用于对检索结果按综合得分排序，并去重，返回最终的候选需求列表
def rank_documents(
    documents: list[ScoredDocument],
    weights: RetrievalWeights,
    limit: int,
) -> list[RequirementCandidate]:
    deduplicated: dict[tuple[str, int], tuple[ScoredDocument, float]] = {}
    for document in documents:
        #去重：以 (需求编号requirement_key, 版本号version_number) 作为唯一标识，保留综合得分最高的文档
        key = (document.requirement_key, document.version_number)
        score = fuse_score(document, weights)
        previous = deduplicated.get(key)
        if previous is None or score > previous[1]:
            deduplicated[key] = (document, score)
    # 按综合得分降序排序，并取前 limit 个结果
    ranked = sorted(deduplicated.values(), key=lambda item: item[1], reverse=True)
    return [#这里使用 Pydantic，把内部检索结果转换成后续冲突分析节点可用的标准对象。
        RequirementCandidate.model_validate(
            {
                "requirement_key": document.requirement_key,#需求唯一标识
                "version_number": document.version_number,#需求版本号
                "title": document.title,#需求标题
                "functional_modules": document.functional_modules,#需求所属的功能模块列表
                "features": document.features,#需求的特性列表，每个特性是一个字典，包含特性名称、描述等信息
                "similarity_score": score,#混合检索得分，表示该检索结果与查询的相似度
                "matched_text": document.content[:1_000],#传给 LLM 的内容摘要
                "sources": document.sources,#需求来源列表
            }
        )
        for document, score in ranked[:limit]
    ]


class HybridRetriever:
    def __init__(
        self,
        session: AsyncSession,#数据库会话对象，异步访问 PostgreSQL / pgvector，用于执行 SQL 查询
        embedding_model: EmbeddingModel,#向量模型对象，用于将查询文本转换为向量表示
        weights: RetrievalWeights,#三种得分的融合权重
        *,
        candidate_limit: int,#限制返回的候选需求数量
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
            # 调用向量模型，将查询文本转换为向量表示
            query_embedding = (await self._embedding_model.embed([query]))[0]
        except Exception as exc:# 如果向量模型调用失败，记录错误信息到数据库，并重新抛出异常
            if source_record_id is not None:
                #记录向量化失败的情况到数据库，方便后续分析和排查问题
                await self._record_embedding(
                    source_record_id,
                    query,
                    None,
                    started,
                    f"{type(exc).__name__}: {exc}",
                )
            raise
        if source_record_id is not None:
            #记录向量化成功的情况到数据表analysis_result，方便后续分析和排查问题
            #这里记录的是本次调用信息，例如模型名称、输入、耗时、向量维度和错误信息。
            #不会把查询向量本身保存到 AnalysisResult 中，真正用于历史需求检索的向量，已经在 RequirementEmbedding 表中
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
        #关键字匹配得分：使用 PostgreSQL 的全文搜索功能，计算查询文本与需求标题和内容的匹配度
        search_text = RequirementEmbedding.title + literal(" ") + RequirementEmbedding.content#历史需求标题 + 空格 + 历史需求正文
        search_vector = func.to_tsvector("simple", search_text)#将历史需求标题和正文转换为文本搜索向量
        search_query = func.plainto_tsquery("simple", query)#将查询文本转换为文本搜索查询向量
        keyword_score = cast(func.ts_rank_cd(search_vector, search_query), Float)#计算查询文本与需求标题和内容的匹配度，返回一个浮点数，表示匹配得分
        #这里使用的 simple 是 PostgreSQL 的简单分词配置。更接近按符号和空白做基础切分。

        #向量匹配得分：计算查询向量与需求向量的余弦相似度
        vector_score = cast(
            1 - RequirementEmbedding.embedding.cosine_distance(query_embedding),
            Float,
        )
        #业务匹配得分：如果候选需求模块在已有需求模块中，则得分为 1，否则为 0
        business_score = case(
            (RequirementEmbedding.module.in_(query_modules), 1.0),
            else_=0.0,
        )
        clauses: list[ColumnElement[bool]] = []#
        #添加过滤条件
        #按需求状态过滤，例如只检索已生效需求。
        if filters.statuses:
            clauses.append(RequirementEmbedding.status.in_(filters.statuses))
        #按需求模块过滤，按模块过滤有两种命中方式：文档主模块 module 命中；或者文档功能模块 functional_modules 命中。
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
            .join(#只查询当前正式版本的需求，避免历史版本干扰检索结果
                Requirement,
                Requirement.id == RequirementEmbedding.requirement_id,
            )
            .where(Requirement.current_version_id == RequirementEmbedding.version_id)
            .where(*clauses)
            .order_by(#按综合得分降序排序，综合得分 = 关键词匹配得分 * 权重 + 向量匹配得分 * 权重 + 业务匹配得分 * 权重
                (
                    self._weights.keyword * keyword_score
                    + self._weights.vector * vector_score
                    + self._weights.business * business_score
                ).desc()
            )
            .limit(self._candidate_limit * 3)
            #这里取候选数量的 3 倍，是因为同一条需求版本可能有多个向量文档，
            #如果直接只取前 K 条文档，可能被同一条需求占满。先取 3K 条，后面再按需求版本去重，能提升最终候选列表的多样性。
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
                source_record_id=source_record_id,#记录源 ID，用于关联到原始需求记录
                analysis_type=AnalysisType.EMBEDDING,#分析类型为向量化embedding
                model_name=self._embedding_model.model_name,#调用模型名称
                prompt_version="embedding-v1",#prompt 版本号，表示使用的向量化模型版本
                input_snapshot={"texts": [query]},#输入快照，记录原始查询文本
                result_json=(#记录向量化结果的元数据，包括向量数量和维度，如果向量化失败则为 None
                    {"vector_count": 1, "dimension": len(embedding)}
                    if embedding is not None
                    else None
                ),
                raw_output=None,#记录原始输出，向量化模型的原始返回结果，如果向量化失败则为 None
                confidence=None,#记录置信度，向量化模型的置信度评分，如果向量化失败则为 None
                duration_ms=max(0, round((perf_counter() - started) * 1000)),#记录向量化耗时，单位为毫秒
                error_message=error_message,#记录错误信息，如果向量化失败则为异常信息，否则为 None
                attempt_number=1,#记录尝试次数，表示这是第几次尝试向量化，如果向量化失败则为 1
            )
        )
        await self._session.commit()
