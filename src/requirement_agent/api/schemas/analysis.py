from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from requirement_agent.ai.schemas.retrieval import RequirementCandidate
from requirement_agent.shared.enums import AnalysisType


class AnalysisResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_record_id: int
    analysis_type: AnalysisType
    model_name: str
    prompt_version: str
    input_snapshot: dict[str, object]
    result_json: dict[str, object] | None
    raw_output: str | None
    confidence: float | None
    duration_ms: int
    error_message: str | None
    attempt_number: int
    created_at: datetime


class AnalysisResultListResponse(BaseModel):
    items: list[AnalysisResultResponse]
    total: int
    page: int
    page_size: int


class ReanalyzeResponse(BaseModel):
    source_record_id: int
    queued: bool


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)
    source_record_id: int | None = Field(default=None, gt=0)
    query_modules: list[str] = Field(default_factory=list)
    filter_modules: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    items: list[RequirementCandidate]

