from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from requirement_agent.shared.enums import ChannelType

#“多渠道需求输入”的数据模型。它位于 domain/sources/entities.py，
#作用是让网页表单、PDF、Word、图片等不同来源，都以统一、可校验的数据结构进入 Connector 和 IngestionService。

#这些都是 Pydantic 模型，因此创建对象时会自动做类型、长度和必填项校验。

class AttachmentInput(BaseModel):#“待进入系统的附件”，例如 PDF、Word、JPG、PNG。
    model_config = ConfigDict(frozen=True)

    file_name: str = Field(min_length=1, max_length=512)
    file_type: str = Field(min_length=1, max_length=128)
    content: bytes = Field(min_length=1)

#Connector 统一输出的需求对象
#不管来源是网页、文档、图片还是飞书，Connector 最终都要转成 RawSourceInput。
#这样后续入库逻辑只处理一种模型，不需要关心来源差异。
class RawSourceInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel_type: ChannelType#渠道类型
    external_event_id: str = Field(min_length=1, max_length=255)#本次提交的唯一事件 ID，用于幂等控制
    submitter_id: str = Field(min_length=1, max_length=255)#提交人 ID
    submitter_name: str = Field(min_length=1, max_length=255)#提交人姓名
    raw_text: str = ""#原始文本需求
    raw_metadata: dict[str, object] = Field(default_factory=dict)#原始元数据，通常是 JSON 字符串
    received_at: datetime#接收时间


class WebFormConnectorRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    external_event_id: str
    submitter_id: str
    submitter_name: str
    raw_text: str
    raw_metadata: dict[str, object] = Field(default_factory=dict)
    received_at: datetime


class FileConnectorRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    external_event_id: str
    submitter_id: str
    submitter_name: str
    raw_text: str = ""
    raw_metadata: dict[str, object] = Field(default_factory=dict)
    received_at: datetime
    attachment: AttachmentInput

