import pytest
from pydantic import ValidationError

from requirement_agent.shared.config import Settings


def test_retrieval_weights_must_sum_to_one() -> None:
    with pytest.raises(ValidationError, match="retrieval weights must sum to 1.0"):
        Settings(
            retrieval_keyword_weight=0.8,
            retrieval_vector_weight=0.4,
            retrieval_business_weight=0.2,
        )


def test_candidate_limit_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(retrieval_candidate_limit=21)


def test_recent_conversation_context_limit_defaults_to_three() -> None:
    assert Settings.model_fields["conversation_context_recent_message_limit"].default == 3


def test_llm_output_budget_and_timeout_defaults() -> None:
    assert Settings.model_fields["llm_max_completion_tokens"].default == 4096
    assert Settings.model_fields["llm_timeout_seconds"].default == 120


def test_llm_output_budget_and_timeout_read_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MAX_COMPLETION_TOKENS", "8192")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "180")

    settings = Settings()

    assert settings.llm_max_completion_tokens == 8192
    assert settings.llm_timeout_seconds == 180


@pytest.mark.parametrize("value", [255, 32_769])
def test_llm_output_budget_is_bounded(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(llm_max_completion_tokens=value)


@pytest.mark.parametrize("value", [0, 601])
def test_llm_timeout_is_bounded(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(llm_timeout_seconds=value)


def test_min_similarity_score_defaults_to_point_four() -> None:
    assert Settings.model_fields["retrieval_min_similarity_score"].default == 0.40


def test_min_similarity_score_reads_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETRIEVAL_MIN_SIMILARITY_SCORE", "0.72")

    assert Settings().retrieval_min_similarity_score == 0.72


@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_min_similarity_score_is_bounded(value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(retrieval_min_similarity_score=value)
