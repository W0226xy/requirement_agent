from typing import Annotated, Literal

from pydantic import Field, model_validator

from requirement_agent.ai.schemas.common import StrictAIModel
from requirement_agent.shared.enums import (
    ChangeOperation,
    ConflictStatus,
    ConflictType,
    ProbabilityLevel,
    RiskLevel,
)


class RequirementEntities(StrictAIModel):
    platform: str | None
    page: str | None
    target: str | None
    actors: list[str]


class RequirementExtraction(StrictAIModel):
    requirement_summary: Annotated[str, Field(min_length=1, max_length=500)]
    requirement_description: Annotated[str, Field(min_length=1, max_length=20_000)]
    functional_modules: list[str]
    acceptance_criteria: list[str]
    clarification_questions: list[str]
    entities: RequirementEntities


class FeatureContent(StrictAIModel):
    module: Annotated[str, Field(min_length=1, max_length=255)]
    feature_title: Annotated[str, Field(min_length=1, max_length=500)]
    feature_description: Annotated[str, Field(min_length=1, max_length=10_000)]
    acceptance_criteria: list[str]


class ProposedOperation(StrictAIModel):
    operation: ChangeOperation
    feature_key: str | None
    content: FeatureContent | None
    source_record_id: Annotated[int | None, Field(gt=0)] = None
    reason: Annotated[str, Field(min_length=1, max_length=2_000)]

    @model_validator(mode="after")
    def validate_operation_shape(self) -> "ProposedOperation":
        if self.operation == ChangeOperation.ADD:
            if self.feature_key is not None or self.content is None:
                raise ValueError("add requires null feature_key and non-null content")
        elif self.operation in {ChangeOperation.MODIFY, ChangeOperation.RESTORE}:
            if self.feature_key is None or self.content is None:
                raise ValueError(f"{self.operation.value} requires feature_key and content")
        elif self.operation == ChangeOperation.DELETE:
            if self.feature_key is None or self.content is not None:
                raise ValueError("delete requires feature_key and null content")
        return self


class RequirementConflict(StrictAIModel):
    requirement_id: Annotated[str, Field(min_length=1, max_length=64)]
    type: ConflictType
    description: Annotated[str, Field(min_length=1, max_length=5_000)]
    evidence: Annotated[str, Field(min_length=1, max_length=5_000)]
    confidence: Annotated[float, Field(ge=0, le=1)]


class RequirementRisk(StrictAIModel):
    type: Annotated[str, Field(min_length=1, max_length=128)]
    level: RiskLevel
    description: Annotated[str, Field(min_length=1, max_length=5_000)]
    probability: ProbabilityLevel
    impact: RiskLevel
    mitigation: Annotated[str, Field(min_length=1, max_length=5_000)]


class ConflictAnalysis(StrictAIModel):
    conflict_status: ConflictStatus
    related_requirement_ids: list[str]
    conflicts: list[RequirementConflict]
    risks: list[RequirementRisk]
    proposed_operations: list[ProposedOperation]
    clarification_questions: list[str]

    @model_validator(mode="after")
    def validate_insufficient_information(self) -> "ConflictAnalysis":
        if self.conflict_status == ConflictStatus.INSUFFICIENT_INFO and self.conflicts:
            raise ValueError("insufficient_info cannot include asserted conflicts")
        return self


AnalysisKind = Literal["extraction", "conflict_risk"]

