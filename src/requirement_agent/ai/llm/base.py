from typing import Protocol


class ChatModel(Protocol):
    @property
    def model_name(self) -> str:
        ...

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        analysis_type: str | None = None,
    ) -> str:
        ...


class EmbeddingModel(Protocol):
    @property
    def model_name(self) -> str:
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...
