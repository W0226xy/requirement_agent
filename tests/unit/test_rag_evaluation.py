import json
from pathlib import Path

import pytest

from requirement_agent.ai.schemas.retrieval import RequirementCandidate
from requirement_agent.evaluation.rag import (
    EVALUATION_CONFIGS,
    EvaluationCase,
    EvaluationQuery,
    load_dataset,
    recall_at_k,
    run_evaluation,
)


def test_recall_at_k_single_answer_hit_and_miss() -> None:
    assert recall_at_k(["REQ-1", "REQ-2"], ["REQ-1"], 1) == 1.0
    assert recall_at_k(["REQ-2", "REQ-1"], ["REQ-1"], 1) == 0.0


def test_recall_at_k_multiple_answers_and_unique_top_k() -> None:
    assert recall_at_k(["REQ-X", "REQ-X", "REQ-A", "REQ-B"], ["REQ-A", "REQ-B"], 2) == 0.5
    assert recall_at_k(["REQ-A", "REQ-B", "REQ-C"], ["REQ-A", "REQ-B"], 2) == 1.0


def test_recall_at_k_boundaries() -> None:
    assert recall_at_k([], ["REQ-1"], 3) == 0.0
    assert recall_at_k(["REQ-1"], ["REQ-1"], 0) == 0.0
    assert recall_at_k(["REQ-1"], [], 1) is None


def test_load_dataset_rejects_invalid_jsonl(tmp_path: Path) -> None:
    dataset = tmp_path / "invalid.jsonl"
    dataset.write_text('{"case_id":"x"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid RAG evaluation case"):
        load_dataset(dataset)


class StubRetriever:
    async def search(self, query: str, *, source_record_id: int | None, query_modules: list[str]) -> list[RequirementCandidate]:
        return [
            RequirementCandidate.model_validate({
                "requirement_key": "REQ-B", "version_number": 1, "title": "B",
                "functional_modules": [], "features": [], "similarity_score": 1.0,
                "matched_text": "", "sources": [],
            }),
            RequirementCandidate.model_validate({
                "requirement_key": "REQ-A", "version_number": 1, "title": "A",
                "functional_modules": [], "features": [], "similarity_score": 0.9,
                "matched_text": "", "sources": [],
            }),
        ]


async def test_run_evaluation_passes_each_weight_configuration_and_skips_empty_gold() -> None:
    received = []
    cases = [
        EvaluationCase("scored", EvaluationQuery("summary", "description", ["module"]), ["REQ-A"], "duplicate", "test"),
        EvaluationCase("skipped", EvaluationQuery("summary", "description", ["module"]), [], "unrelated", "test", "no gold"),
    ]

    def factory(weights):
        received.append(weights)
        return StubRetriever()

    report = await run_evaluation(cases, [1, 3], factory)
    assert received == list(EVALUATION_CONFIGS.values())
    assert report["valid_sample_count"] == 1
    assert report["schemes"]["hybrid"]["macro_recall_at_k"] == {"1": 0.0, "3": 1.0}
    assert report["schemes"]["hybrid"]["cases"][1]["recall_at_k"] == {"1": None, "3": None}


async def test_fixed_corpus_generates_complete_report(tmp_path: Path) -> None:
    from requirement_agent.evaluation.rag import evaluate_offline

    dataset = Path("tests/fixtures/rag_eval_cases.jsonl")
    report = await evaluate_offline(dataset, [1, 3, 5, 10])
    output = tmp_path / "nested" / "report.json"
    output.parent.mkdir(parents=True)
    output.write_text(json.dumps(report), encoding="utf-8")
    assert report["dataset_sample_count"] == 21
    assert report["valid_sample_count"] == 20
    assert set(report["schemes"]) == {"keyword_only", "vector_only", "hybrid"}
    assert output.exists()
