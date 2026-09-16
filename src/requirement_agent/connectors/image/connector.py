from typing import Any

from requirement_agent.domain.sources.entities import (
    AttachmentInput,
    FileConnectorRequest,
    RawSourceInput,
)
from requirement_agent.shared.enums import ChannelType

IMAGE_TYPES = {"image/jpeg", "image/png"}#支持的图片类型


class ImageConnector:
    def __init__(self) -> None:
        self._attachment: AttachmentInput | None = None#用来暂时保存当前请求中的图片附件

    async def verify(self, request: Any) -> bool:#初步校验图片请求
        return (
            isinstance(request, FileConnectorRequest)#检查请求类型是否为 FileConnectorRequest
            and request.attachment.file_type in IMAGE_TYPES#检查附件的文件类型是否在支持的图片类型集合中
        )

    async def receive(self, request: Any) -> RawSourceInput:#把图片请求转换成系统内部的 RawSourceInput 原始需求对象
        if not isinstance(request, FileConnectorRequest):
            raise TypeError("request must be a FileConnectorRequest")
        self._attachment = request.attachment
        return RawSourceInput(
            channel_type=ChannelType.IMAGE,
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

