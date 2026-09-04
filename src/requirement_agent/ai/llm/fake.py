import hashlib
import json
from collections import deque


class FakeLLM:
    def __init__(
        self,
        responses: list[str | dict[str, object]],
        *,
        embedding_dimension: int = 3,
        model_name: str = "fake-llm",
    ) -> None:
        self._responses = deque(
            response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
            for response in responses
        )
        self._embedding_dimension = embedding_dimension
        self._model_name = model_name
        self.calls: list[list[dict[str, str]]] = []

    @property
    def model_name(self) -> str:
        return self._model_name

    async def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        if not self._responses:
            raise RuntimeError("FakeLLM has no response configured")
        return self._responses.popleft()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for text in texts:
            values = [
                (
                    hashlib.sha256(f"{text}:{index}".encode()).digest()[0]
                    / 127.5
                )
                - 1
                for index in range(self._embedding_dimension)
            ]
            results.append(values)
        return results
