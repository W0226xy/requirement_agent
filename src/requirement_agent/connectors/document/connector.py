from typing import Any

from requirement_agent.domain.sources.entities import (
    AttachmentInput,
    FileConnectorRequest,
    RawSourceInput,
)
from requirement_agent.shared.enums import ChannelType

DOCUMENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class DocumentConnector:
    def __init__(self) -> None:
        self._attachment: AttachmentInput | None = None

    async def verify(self, request: Any) -> bool:
        return (
            isinstance(request, FileConnectorRequest)
            and request.attachment.file_type in DOCUMENT_TYPES
        )

    async def receive(self, request: Any) -> RawSourceInput:
        if not isinstance(request, FileConnectorRequest):
            raise TypeError("request must be a FileConnectorRequest")
        self._attachment = request.attachment
        return RawSourceInput(
            channel_type=ChannelType.DOCUMENT,
            external_event_id=request.external_event_id,
            submitter_id=request.submitter_id,
            submitter_name=request.submitter_name,
            raw_text=request.raw_text,
            raw_metadata=request.raw_metadata,
            received_at=request.received_at,
        )

    async def download_attachments(
        self,
        source: RawSourceInput,
    ) -> list[AttachmentInput]:
        if self._attachment is None:
            raise RuntimeError("receive must be called before download_attachments")
        return [self._attachment]

