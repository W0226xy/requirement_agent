from typing import Any

from requirement_agent.domain.sources.entities import (
    AttachmentInput,
    RawSourceInput,
    WebFormConnectorRequest,
)
from requirement_agent.shared.enums import ChannelType


class WebFormConnector:
    async def verify(self, request: Any) -> bool:
        return isinstance(request, WebFormConnectorRequest) and bool(request.raw_text.strip())

    async def receive(self, request: Any) -> RawSourceInput:
        if not isinstance(request, WebFormConnectorRequest):
            raise TypeError("request must be a WebFormConnectorRequest")
        return RawSourceInput(
            channel_type=ChannelType.WEB_FORM,
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
        return []

