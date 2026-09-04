from requirement_agent.ai.llm import factory
from requirement_agent.ai.llm.openai_compatible import (
    OpenAICompatibleChatModel,
    OpenAICompatibleEmbeddingModel,
)
from requirement_agent.shared.config import Settings


def test_factories_create_separate_client_types(monkeypatch: object) -> None:
    settings = Settings(
        llm_base_url="https://chat.example/v1",
        llm_api_key="chat-secret",
        llm_model="chat-model",
        embedding_base_url="https://embedding.example/v1",
        embedding_api_key="embedding-secret",
        embedding_model="embedding-model",
        embedding_dimension=3,
    )
    monkeypatch.setattr(factory, "get_settings", lambda: settings)
    factory.get_llm.cache_clear()
    factory.get_embedding_model.cache_clear()
    try:
        chat = factory.get_llm()
        embedding = factory.get_embedding_model()
    finally:
        factory.get_llm.cache_clear()
        factory.get_embedding_model.cache_clear()

    assert isinstance(chat, OpenAICompatibleChatModel)
    assert isinstance(embedding, OpenAICompatibleEmbeddingModel)
    assert chat is not embedding
    assert chat.model_name == "chat-model"
    assert embedding.model_name == "embedding-model"

