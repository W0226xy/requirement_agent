from datetime import UTC, datetime
from typing import Any

from requirement_agent.connectors.feishu.client import FeishuOpenAPIClient
from requirement_agent.connectors.feishu.models import (
    FeishuEventRequest,
    FeishuFileContent,
    FeishuImageContent,
    FeishuTextContent,
)
from requirement_agent.domain.sources.entities import AttachmentInput, RawSourceInput
from requirement_agent.shared.enums import ChannelType

SUPPORTED_FILE_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}


class FeishuConnector:
    def __init__(self, client: FeishuOpenAPIClient | None = None) -> None:
        self._client = client
        self._attachment: tuple[str, str, str, str] | None = None

    async def verify(self, request: Any) -> bool:
        return (
            isinstance(request, FeishuEventRequest)
            and request.header.event_type == "im.message.receive_v1"
        )

    async def receive(self, request: Any) -> RawSourceInput:
        if not isinstance(request, FeishuEventRequest):
            raise TypeError("request must be a FeishuEventRequest")

        message = request.event.message
        raw_text = ""
        if message.message_type == "text":
            raw_text = FeishuTextContent.model_validate_json(message.content).text
        elif message.message_type == "image":
            image_content = FeishuImageContent.model_validate_json(message.content)
            self._attachment = (
                message.message_id,
                image_content.image_key,
                "image",
                f"{message.message_id}.png",
            )
        else:
            file_content = FeishuFileContent.model_validate_json(message.content)
            self._attachment = (
                message.message_id,
                file_content.file_key,
                "file",
                file_content.file_name,
            )

        sender = request.event.sender
        sender_id = (
            sender.sender_id.user_id
            or sender.sender_id.open_id
            or sender.sender_id.union_id
        )
        assert sender_id is not None
        timestamp = float(request.header.create_time)
        if timestamp > 10_000_000_000:
            timestamp /= 1000

        return RawSourceInput(
            channel_type=ChannelType.FEISHU,
            external_event_id=request.header.event_id,
            submitter_id=sender_id,
            submitter_name=sender_id,
            raw_text=raw_text,
            raw_metadata={
                "event_type": request.header.event_type,
                "message_id": message.message_id,
                "message_type": message.message_type,
                "chat_id": message.chat_id,
                "chat_type": message.chat_type,
                "sender_type": sender.sender_type,
                "sender_ids": sender.sender_id.model_dump(exclude_none=True),
                "tenant_key": request.header.tenant_key,
            },
            received_at=datetime.fromtimestamp(timestamp, UTC),
        )

    async def download_attachments(
        self,
        source: RawSourceInput,
    ) -> list[AttachmentInput]:
        if self._attachment is None:
            return []
        if self._client is None:
            raise RuntimeError("a Feishu API client is required for attachments")
        message_id, resource_key, resource_type, file_name = self._attachment
        content, content_type = await self._client.download_resource(
            message_id=message_id,
            resource_key=resource_key,
            resource_type=resource_type,
        )
        content_type = _resolve_file_type(content, content_type, file_name)
        if resource_type == "image":
            file_name = f"{message_id}{SUPPORTED_FILE_TYPES.get(content_type, '.img')}"
        return [
            AttachmentInput(
                file_name=file_name,
                file_type=content_type,
                content=content,
            )
        ]


def _resolve_file_type(content: bytes, reported_type: str, file_name: str) -> str:
    if reported_type in SUPPORTED_FILE_TYPES:
        return reported_type
    suffix = file_name.lower().rsplit(".", maxsplit=1)[-1]
    if content.startswith(b"%PDF-") and suffix == "pdf":
        return "application/pdf"
    if content.startswith(b"PK") and suffix == "docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return reported_type
