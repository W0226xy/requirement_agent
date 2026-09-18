from typing import Protocol

from pydantic import BaseModel


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: str


class ToolChatResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = []


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

    async def complete_with_tools(
        self, messages: list[dict[str, object]], *, tools: list[dict[str, object]]
    ) -> ToolChatResponse:
        ...


class EmbeddingModel(Protocol):
    @property
    def model_name(self) -> str:
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...
