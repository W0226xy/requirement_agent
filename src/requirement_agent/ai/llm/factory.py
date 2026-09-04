from functools import lru_cache

from requirement_agent.ai.llm.openai_compatible import (
    OpenAICompatibleChatModel,
    OpenAICompatibleEmbeddingModel,
)
from requirement_agent.shared.config import get_settings


@lru_cache
def get_llm() -> OpenAICompatibleChatModel:
    settings = get_settings()
    return OpenAICompatibleChatModel(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )


@lru_cache
def get_embedding_model() -> OpenAICompatibleEmbeddingModel:
    settings = get_settings()
    return OpenAICompatibleEmbeddingModel(
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
        timeout_seconds=settings.llm_timeout_seconds,
    )
