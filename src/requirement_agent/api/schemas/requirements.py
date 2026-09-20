from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from requirement_agent.shared.enums import (
    FeatureStatus,
    LineageOperationType,
    RequirementChangeType,
    RequirementStatus,
)


class FeatureLineageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_record_id: int
    introduced_version_id: int
    operation_type: LineageOperationType
    evidence_text: str
    created_at: datetime


class RequirementFeatureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    feature_key: str
    module: str
    feature_title: str
    feature_description: str
    acceptance_criteria: list[str]
    feature_status: FeatureStatus
    sort_order: int
    lineage: list[FeatureLineageResponse] = Field(default_factory=list)


class RequirementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requirement_key: str
    title: str
    current_version_id: int | None
    current_version_number: int | None = None
    status: RequirementStatus
    functional_modules: list[str]
    extra_fields: dict[str, object]
    created_at: datetime
    updated_at: datetime
    features: list[RequirementFeatureResponse] = Field(default_factory=list)


class RequirementListResponse(BaseModel):
    items: list[RequirementResponse]
    total: int
    page: int
    page_size: int


class RequirementModuleListResponse(BaseModel):
    items: list[str]


class RequirementOverviewItemResponse(BaseModel):
    id: int
    requirement_key: str
    title: str
    description: str
    status: RequirementStatus
    source_record_id: int | None = None


class RequirementOverviewModuleResponse(BaseModel):
    name: str
    requirement_count: int
    status_counts: dict[str, int]
    requirements: list[RequirementOverviewItemResponse]
    module_overview: "ModuleOverviewResponse | None" = None


class ModuleOverviewResponse(BaseModel):
    overview: str | None = None
    core_capabilities: list[str] = Field(default_factory=list)
    pending_items: list[str] = Field(default_factory=list)
    status: str
    updated_at: datetime | None = None
    referenced_requirement_keys: list[str] = Field(default_factory=list)
    last_error: str | None = None


class ModuleOverviewRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    overview: str
    core_capabilities: list[str]
    pending_items: list[str]
    source_snapshot: list[dict[str, object]]
    source_fingerprint: str
    trigger_requirement_key: str | None
    trigger_version_number: int | None
    created_at: datetime


class ModuleOverviewHistoryResponse(BaseModel):
    items: list[ModuleOverviewRevisionResponse]


class RequirementOverviewResponse(BaseModel):
    total_requirements: int
    modules: list[RequirementOverviewModuleResponse]


class RequirementVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requirement_id: int
    version_number: int
    parent_version_id: int | None
    change_type: RequirementChangeType
    version_title: str
    requirement_snapshot: dict[str, object]
    diff_snapshot: dict[str, object]
    change_reason: str
    created_by: str
    reviewed_by: str
    created_at: datetime
    features: list[RequirementFeatureResponse] = Field(default_factory=list)


class RequirementVersionListResponse(BaseModel):
    items: list[RequirementVersionResponse]


class RequirementDiffResponse(BaseModel):
    requirement_id: int
    from_version: int | None
    to_version: int
    diff: dict[str, object]
