from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from requirement_agent.shared.enums import ChannelType


class AttachmentInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    file_name: str = Field(min_length=1, max_length=512)
    file_type: str = Field(min_length=1, max_length=128)
    content: bytes = Field(min_length=1)


class RawSourceInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel_type: ChannelType
    external_event_id: str = Field(min_length=1, max_length=255)
    submitter_id: str = Field(min_length=1, max_length=255)
    submitter_name: str = Field(min_length=1, max_length=255)
    raw_text: str = ""
    raw_metadata: dict[str, object] = Field(default_factory=dict)
    received_at: datetime


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

