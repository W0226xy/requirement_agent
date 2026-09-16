from typing import Any

from requirement_agent.domain.sources.entities import (
    AttachmentInput,
    RawSourceInput,
    WebFormConnectorRequest,
)
from requirement_agent.shared.enums import ChannelType
#网页表单渠道的 Connector。它的作用是把前端提交的一段普通文本需求，
#校验后统一转换成系统内部的 RawSourceInput 原始需求对象。

class WebFormConnector:
    #网页表单渠道的 Connector。它的作用是把前端提交的一段普通文本需求，

    async def verify(self, request: Any) -> bool:
        return isinstance(request, WebFormConnectorRequest) and bool(request.raw_text.strip())

    # 校验后统一转换成系统内部的 RawSourceInput 原始需求对象。
    async def receive(self, request: Any) -> RawSourceInput:
        if not isinstance(request, WebFormConnectorRequest):
            raise TypeError("request must be a WebFormConnectorRequest")
        return RawSourceInput(
            channel_type=ChannelType.WEB_FORM,#网页表单渠道
            external_event_id=request.external_event_id,#本次提交的唯一事件 ID，用于幂等控制
            submitter_id=request.submitter_id,#提交人 ID
            submitter_name=request.submitter_name,#提交人姓名
            raw_text=request.raw_text,#网页表单提交的原始文本需求
            raw_metadata=request.raw_metadata,#网页表单提交的原始元数据，通常是 JSON 字符串
            received_at=request.received_at,#网页表单提交的时间戳
        )

    async def download_attachments(
        self,
        source: RawSourceInput,
    ) -> list[AttachmentInput]:#网页表单本身没有附件，所以它的附件方法固定返回空列表。
        return []

