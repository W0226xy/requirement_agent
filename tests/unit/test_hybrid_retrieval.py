import pytest

from requirement_agent.ai.retrieval.hybrid import (
    RetrievalWeights,
    ScoredDocument,
    rank_documents,
)


def document(
    key: str,
    version: int,
    *,
    keyword: float,
    vector: float,
    business: float,
) -> ScoredDocument:
    return ScoredDocument(
        requirement_key=key,
        version_number=version,
        title=f"Requirement {key}",
        functional_modules=["reporting"],
        features=[],
        sources=[],
        content=f"Matched text for {key}",
        keyword_score=keyword,
        vector_score=vector,
        business_score=business,
    )


def test_weighted_ranking_and_deduplication() -> None:
    weights = RetrievalWeights(keyword=0.4, vector=0.4, business=0.2)
    results = rank_documents(
        [
            document("REQ-001", 1, keyword=0.9, vector=0.8, business=1),
            document("REQ-001", 1, keyword=0.1, vector=0.1, business=0),
            document("REQ-002", 1, keyword=0.7, vector=0.6, business=0),
        ],
        weights,
        limit=20,
    )

    assert [item.requirement_key for item in results] == ["REQ-001", "REQ-002"]
    assert results[0].similarity_score == pytest.approx(0.88)


def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValueError):
        RetrievalWeights(keyword=0.6, vector=0.6, business=0.2)


def test_final_similarity_threshold_precedes_sorting_and_limit() -> None:
    weights = RetrievalWeights(keyword=0.4, vector=0.4, business=0.2)
    results = rank_documents(
        [
            document("REQ-039", 1, keyword=0.39, vector=0.39, business=0.39),
            document("REQ-040", 1, keyword=0.40, vector=0.40, business=0.40),
            document("REQ-072", 1, keyword=0.72, vector=0.72, business=0.72),
            document("REQ-091", 1, keyword=0.91, vector=0.91, business=0.91),
        ],
        weights,
        limit=2,
        min_similarity_score=0.40,
    )

    assert [item.requirement_key for item in results] == ["REQ-091", "REQ-072"]
    assert [item.similarity_score for item in results] == pytest.approx([0.91, 0.72])


def test_final_similarity_threshold_keeps_boundary_score() -> None:
    weights = RetrievalWeights(keyword=0.4, vector=0.4, business=0.2)
    results = rank_documents(
        [
            document("REQ-039", 1, keyword=0.39, vector=0.39, business=0.39),
            document("REQ-040", 1, keyword=0.40, vector=0.40, business=0.40),
            document("REQ-072", 1, keyword=0.72, vector=0.72, business=0.72),
        ],
        weights,
        limit=20,
        min_similarity_score=0.40,
    )

    assert [item.requirement_key for item in results] == ["REQ-072", "REQ-040"]
