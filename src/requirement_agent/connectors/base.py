from typing import Any, Protocol

from requirement_agent.domain.sources.entities import AttachmentInput, RawSourceInput


class SourceConnector(Protocol):
    async def verify(self, request: Any) -> bool:
        ...

    async def receive(self, request: Any) -> RawSourceInput:
        ...

    async def download_attachments(
        self,
        source: RawSourceInput,
    ) -> list[AttachmentInput]:
        ...

