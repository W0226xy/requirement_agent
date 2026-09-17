import logging
from collections.abc import Mapping, Sequence

import httpx

from requirement_agent.shared.errors import (
    LLMEmptyContentError,
    LLMOutputBudgetExceededError,
    LLMServiceError,
)

logger = logging.getLogger(__name__)

# LLM / Embedding 模型适配层。它的作用是：把不同厂商但兼容 OpenAI API 格式的模型服务，统一封装成项目内部可调用的两个能力：
# OpenAICompatibleChatModel：调用大模型生成结构化需求分析结果。
# OpenAICompatibleEmbeddingModel：调用向量模型，把文本转换为 embedding 向量。


class _OpenAICompatibleClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def _post(
        self, path: str, payload: dict[str, object]
    ) -> tuple[dict[str, object], int]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._base_url}{path}",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            status_code = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None
                else None
            )
            response_body = (
                exc.response.text[:1_000]
                if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None
                else None
            )
            logger.warning(
                "model_service_request_failed http_status=%s error_type=%s "
                "response_body=%r",
                status_code,
                type(exc).__name__,
                response_body,
            )
            raise LLMServiceError(
                f"model service request failed: {type(exc).__name__}"
            ) from None
        if not isinstance(body, dict):
            raise LLMServiceError("model service response must be a JSON object")
        return body, response.status_code


class OpenAICompatibleChatModel(_OpenAICompatibleClient):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_tokens: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
        self._model = model
        self._max_tokens = max_tokens

    @property
    def model_name(self) -> str:
        return self._model

    #complete()：调用聊天模型，传入消息列表，返回模型生成的文本内容。它会检查响应的结构是否符合预期，如果不符合，会抛出 LLMServiceError 异常。
    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        analysis_type: str | None = None,
    ) -> str:
        payload: dict[str, object] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self._max_tokens,
        }
        # Conversation summaries are deliberately plain, headed text rather than a
        # JSON document.  Some OpenAI-compatible providers reject json_object mode
        # when the prompt does not ask for JSON.
        if analysis_type != "conversation_summary":
            payload["response_format"] = {"type": "json_object"}
        response, status_code = await self._post("/chat/completions", payload)
        try:
            choices = response["choices"]
            if not isinstance(choices, Sequence) or isinstance(choices, str | bytes):
                raise TypeError("choices must be an array")
            choice = choices[0]
            if not isinstance(choice, Mapping):
                raise TypeError("choice must be an object")
            message = choice["message"]
            if not isinstance(message, Mapping):
                raise TypeError("message must be an object")
            finish_reason = choice.get("finish_reason")
            if finish_reason is not None and not isinstance(finish_reason, str):
                raise TypeError("finish_reason must be a string or null")
            content = message.get("content", _MISSING)
            content_state = _content_state(content)
            reasoning = message.get("reasoning")
            usage = response.get("usage")
            self._log_chat_response(
                status_code=status_code,
                analysis_type=analysis_type,
                finish_reason=finish_reason,
                choices_count=len(choices),
                content_state=content_state,
                content=content,
                reasoning=reasoning,
                usage=usage,
                top_level_keys=list(response),
            )
            if content_state != "nonempty":
                if finish_reason == "length":
                    raise LLMOutputBudgetExceededError(
                        "model output budget exhausted before final content was generated"
                    )
                raise LLMEmptyContentError(
                    "model service returned empty content"
                )
            if not isinstance(content, str):
                raise TypeError("content must be a string")
            return content
        except (KeyError, IndexError, TypeError):
            raise LLMServiceError(
                "chat response has an invalid OpenAI-compatible shape"
            ) from None

    def _log_chat_response(
        self,
        *,
        status_code: int,
        analysis_type: str | None,
        finish_reason: str | None,
        choices_count: int,
        content_state: str,
        content: object,
        reasoning: object,
        usage: object,
        top_level_keys: list[str],
    ) -> None:
        usage_mapping = usage if isinstance(usage, Mapping) else {}
        completion_details = usage_mapping.get("completion_tokens_details")
        details_mapping = (
            completion_details if isinstance(completion_details, Mapping) else {}
        )
        logger.info(
            "chat_completion_response analysis_type=%s model=%s http_status=%s finish_reason=%s "
            "choices_count=%s content_state=%s raw_output_length=%s reasoning_length=%s "
            "prompt_tokens=%s completion_tokens=%s reasoning_tokens=%s top_level_keys=%s",
            analysis_type,
            self._model,
            status_code,
            finish_reason,
            choices_count,
            content_state,
            len(content) if isinstance(content, str) else 0,
            len(reasoning) if isinstance(reasoning, str) else 0,
            usage_mapping.get("prompt_tokens"),
            usage_mapping.get("completion_tokens"),
            details_mapping.get("reasoning_tokens"),
            sorted(top_level_keys),
        )


# Embedding 模型适配层。它的作用是：把不同厂商但兼容 OpenAI API 格式的向量模型服务，统一封装成项目内部可调用的能力：
class OpenAICompatibleEmbeddingModel(_OpenAICompatibleClient):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimension: int,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
        self._model = model
        self._dimension = dimension

    @property
    def model_name(self) -> str:
        return self._model
    # embed()：调用向量模型，把文本列表转换为 embedding 向量列表。它会检查响应的结构是否符合预期，如果不符合，会抛出 LLMServiceError 异常。
    async def embed(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, object] = {
            "model": self._model,
            "input": texts,
        }
        response, _ = await self._post("/embeddings", payload)
        try:
            data = response["data"]
            if not isinstance(data, Sequence) or isinstance(data, str | bytes):
                raise TypeError("data must be an array")
            ordered = sorted(data, key=self._embedding_index)
            embeddings = [self._embedding_values(item) for item in ordered]
        except (KeyError, TypeError, ValueError):
            raise LLMServiceError("embedding response has an invalid shape") from None
        if len(embeddings) != len(texts):
            raise LLMServiceError("embedding response count does not match input count")
        if any(len(item) != self._dimension for item in embeddings):
            raise LLMServiceError("embedding response has an unexpected dimension")
        return embeddings

    @staticmethod
    def _embedding_index(item: object) -> int:
        if not isinstance(item, Mapping) or not isinstance(item.get("index"), int):
            raise TypeError("embedding item requires an integer index")
        return int(item["index"])

    @staticmethod
    def _embedding_values(item: object) -> list[float]:
        if not isinstance(item, Mapping):
            raise TypeError("embedding item must be an object")
        values = item.get("embedding")
        if not isinstance(values, Sequence) or isinstance(values, str | bytes):
            raise TypeError("embedding must be an array")
        return [float(value) for value in values]


_MISSING = object()


def _content_state(content: object) -> str:
    if content is _MISSING:
        return "missing"
    if content is None:
        return "null"
    if content == "":
        return "empty"
    return "nonempty"
