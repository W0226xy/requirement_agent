import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FeishuModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FeishuURLVerificationRequest(FeishuModel):
    challenge: str = Field(min_length=1, max_length=2048)
    token: str = Field(min_length=1, max_length=2048)
    type: Literal["url_verification"]


class FeishuEventHeader(FeishuModel):
    event_id: str = Field(min_length=1, max_length=255)
    event_type: Literal["im.message.receive_v1"]
    create_time: str = Field(pattern=r"^\d{10,16}$")
    token: str | None = Field(default=None, min_length=1, max_length=2048)
    app_id: str = Field(min_length=1, max_length=255)
    tenant_key: str = Field(min_length=1, max_length=255)


class FeishuSenderID(FeishuModel):
    union_id: str | None = Field(default=None, min_length=1, max_length=255)
    user_id: str | None = Field(default=None, min_length=1, max_length=255)
    open_id: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def require_identifier(self) -> Self:
        if not any((self.union_id, self.user_id, self.open_id)):
            raise ValueError("at least one sender identifier is required")
        return self


class FeishuSender(FeishuModel):
    sender_id: FeishuSenderID
    sender_type: str = Field(min_length=1, max_length=64)
    tenant_key: str = Field(min_length=1, max_length=255)


class FeishuMention(FeishuModel):
    key: str = Field(min_length=1, max_length=255)
    id: FeishuSenderID
    name: str = Field(min_length=1, max_length=255)
    tenant_key: str = Field(min_length=1, max_length=255)


class FeishuTextContent(FeishuModel):
    text: str = Field(min_length=1, max_length=200_000)


class FeishuImageContent(FeishuModel):
    image_key: str = Field(min_length=1, max_length=1024)


class FeishuFileContent(FeishuModel):
    file_key: str = Field(min_length=1, max_length=1024)
    file_name: str = Field(min_length=1, max_length=512)


class FeishuMessage(FeishuModel):
    message_id: str = Field(min_length=1, max_length=255)
    root_id: str | None = Field(default=None, max_length=255)
    parent_id: str | None = Field(default=None, max_length=255)
    thread_id: str | None = Field(default=None, max_length=255)
    create_time: str = Field(pattern=r"^\d{10,16}$")
    update_time: str | None = Field(default=None, pattern=r"^\d{10,16}$")
    chat_id: str = Field(min_length=1, max_length=255)
    chat_type: str = Field(min_length=1, max_length=64)
    message_type: Literal["text", "image", "file"]
    content: str = Field(min_length=2, max_length=200_000)
    mentions: list[FeishuMention] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_content(self) -> Self:
        try:
            decoded = json.loads(self.content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("message content must be valid JSON") from exc
        if self.message_type == "text":
            FeishuTextContent.model_validate(decoded)
        elif self.message_type == "image":
            FeishuImageContent.model_validate(decoded)
        else:
            FeishuFileContent.model_validate(decoded)
        return self


class FeishuMessageEvent(FeishuModel):
    sender: FeishuSender
    message: FeishuMessage


class FeishuEventRequest(FeishuModel):
    schema_: Literal["2.0"] = Field(alias="schema")
    header: FeishuEventHeader
    event: FeishuMessageEvent


class FeishuTokenResponse(FeishuModel):
    code: int
    msg: str
    tenant_access_token: str | None = None
    expire: int | None = Field(default=None, gt=0)
