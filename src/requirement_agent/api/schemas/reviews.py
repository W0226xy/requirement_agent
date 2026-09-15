from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from requirement_agent.ai.schemas.analysis import FeatureContent, ProposedOperation
from requirement_agent.shared.enums import ChangeOperation, ReviewDecision, ReviewStatus


class ReviewOperation(BaseModel):
    operation: ChangeOperation
    feature_key: str | None
    content: FeatureContent | None
    source_record_id: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=2_000)

    def to_domain(self) -> ProposedOperation:
        return ProposedOperation.model_validate(self.model_dump(), strict=False)


class ReviewTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_record_id: int
    target_requirement_id: int | None
    analysis_result_id: int
    review_status: ReviewStatus
    decision: ReviewDecision | None
    extraction_snapshot: dict[str, object]
    candidate_snapshot: list[dict[str, object]]
    analysis_snapshot: dict[str, object]
    approved_operations: list[dict[str, object]] | None
    reviewer_id: str | None
    review_comment: str | None
    reviewed_at: datetime | None
    committed_version_id: int | None
    created_at: datetime
    updated_at: datetime


class ReviewTaskListResponse(BaseModel):
    items: list[ReviewTaskResponse]
    total: int
    page: int
    page_size: int


class ApproveReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ReviewDecision
    title: str | None = Field(default=None, min_length=1, max_length=500)
    target_requirement_key: str | None = Field(default=None, min_length=1, max_length=64)
    expected_requirement_id: int | None = Field(default=None, gt=0)
    expected_current_version: int | None = Field(default=None, ge=1)
    operations: list[ReviewOperation] = Field(min_length=1)
    comment: str | None = Field(default=None, max_length=5_000)

    @model_validator(mode="after")
    def validate_decision(self) -> "ApproveReviewRequest":
        if self.decision == ReviewDecision.CREATE:
            if (
                not self.title
                or self.target_requirement_key is not None
                or self.expected_requirement_id is not None
                or self.expected_current_version is not None
            ):
                raise ValueError("create requires title and no merge target fields")
        elif (
            self.target_requirement_key is None
            or self.expected_requirement_id is None
            or self.expected_current_version is None
        ):
            raise ValueError(
                "merge requires target_requirement_key, expected_requirement_id, "
                "and expected_current_version"
            )
        return self


class ReviewActionRequest(BaseModel):
    comment: str = Field(min_length=1, max_length=5_000)


class ApprovalResponse(BaseModel):
    review_task_id: int
    requirement_id: int
    version_id: int
    version_number: int
