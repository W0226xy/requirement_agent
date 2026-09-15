import json

import httpx
import pytest

from requirement_agent.ai.llm.openai_compatible import (
    OpenAICompatibleChatModel,
    OpenAICompatibleEmbeddingModel,
)
from requirement_agent.shared.errors import (
    LLMEmptyContentError,
    LLMOutputBudgetExceededError,
    LLMServiceError,
)


async def test_chat_and_embedding_use_independent_services_and_keys() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/chat/v1/chat/completions":
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"status":"ok"}'}}]},
            )
        if request.url.path == "/embedding/v1/embeddings":
            return httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    chat = OpenAICompatibleChatModel(
        base_url="https://chat.example/chat/v1",
        api_key="chat-secret",
        model="chat-model",
        timeout_seconds=10,
        max_tokens=4096,
        transport=transport,
    )
    embedding = OpenAICompatibleEmbeddingModel(
        base_url="https://embedding.example/embedding/v1",
        api_key="embedding-secret",
        model="embedding-model",
        dimension=3,
        timeout_seconds=10,
        transport=transport,
    )

    result = await chat.complete([{"role": "user", "content": "hello"}])
    vectors = await embedding.embed(["requirement"])

    assert result == '{"status":"ok"}'
    assert vectors == [[0.1, 0.2, 0.3]]
    assert requests[0].url == "https://chat.example/chat/v1/chat/completions"
    assert requests[0].headers["Authorization"] == "Bearer chat-secret"
    assert json.loads(requests[0].content)["model"] == "chat-model"
    chat_payload = json.loads(requests[0].content)
    assert chat_payload["max_tokens"] == 4096
    assert "max_completion_tokens" not in chat_payload
    assert "stream" not in chat_payload
    assert chat_payload["temperature"] == 0
    assert chat_payload["response_format"] == {"type": "json_object"}
    assert requests[1].url == "https://embedding.example/embedding/v1/embeddings"
    assert requests[1].headers["Authorization"] == "Bearer embedding-secret"
    assert json.loads(requests[1].content)["model"] == "embedding-model"


async def test_embedding_rejects_wrong_dimension() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]},
        )
    )
    embedding = OpenAICompatibleEmbeddingModel(
        base_url="https://embedding.example/v1",
        api_key="embedding-secret",
        model="embedding-model",
        dimension=3,
        timeout_seconds=10,
        transport=transport,
    )

    with pytest.raises(LLMServiceError, match="unexpected dimension"):
        await embedding.embed(["requirement"])


async def test_service_error_does_not_expose_api_key() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, json={"error": "unauthorized"})
    )
    chat = OpenAICompatibleChatModel(
        base_url="https://chat.example/v1",
        api_key="must-not-leak",
        model="chat-model",
        timeout_seconds=10,
        max_tokens=4096,
        transport=transport,
    )

    with pytest.raises(LLMServiceError) as captured:
        await chat.complete([{"role": "user", "content": "hello"}])

    assert "must-not-leak" not in str(captured.value)
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    ("finish_reason", "error_type", "message"),
    [
        (
            "length",
            LLMOutputBudgetExceededError,
            "output budget exhausted",
        ),
        ("stop", LLMEmptyContentError, "returned empty content"),
    ],
)
async def test_chat_classifies_empty_content(
    finish_reason: str,
    error_type: type[LLMServiceError],
    message: str,
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": finish_reason,
                        "message": {"content": "", "reasoning": "internal reasoning"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4096,
                    "completion_tokens_details": {"reasoning_tokens": 4096},
                },
            },
        )
    )
    chat = OpenAICompatibleChatModel(
        base_url="https://chat.example/v1",
        api_key="chat-secret",
        model="chat-model",
        timeout_seconds=10,
        max_tokens=4096,
        transport=transport,
    )

    with pytest.raises(error_type, match=message):
        await chat.complete([{"role": "user", "content": "hello"}])
