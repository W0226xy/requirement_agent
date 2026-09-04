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
