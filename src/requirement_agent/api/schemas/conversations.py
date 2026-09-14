from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from requirement_agent.api.schemas.reviews import ReviewTaskResponse
from requirement_agent.api.schemas.sources import SourceRecordResponse


class CreateConversationRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=255)


class UpdateConversationRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=255)


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    conversation_key: str
    owner_id: str
    title: str
    title_is_custom: bool
    summary: str
    business_context: dict[str, object]
    memory_revision: int
    memory_covered_sequence: int
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse]
    total: int
    page: int
    page_size: int


class ConversationMessageResponse(BaseModel):
    message_key: str
    sequence_number: int
    role: str
    created_at: datetime
    source: SourceRecordResponse
    latest_extraction: dict[str, object] | None
    latest_conflict_analysis: dict[str, object] | None
    review_task: ReviewTaskResponse | None


class ConversationMessageListResponse(BaseModel):
    items: list[ConversationMessageResponse]
    total: int
    page: int
    page_size: int


class CreateConversationMessageResponse(BaseModel):
    message: ConversationMessageResponse
    replayed: bool
