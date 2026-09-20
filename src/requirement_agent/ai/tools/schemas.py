"""Strict contracts exposed to an OpenAI-compatible tool-calling model."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

#定义了 Function Call 工具的输入、输出和错误格式。
# 它相当于工具层的 API 契约，确保模型只能按规定传参，后端也按统一结构返回结果。
class ToolModel(BaseModel):
    #extra="forbid"：不允许模型传未定义字段。
    #str_strip_whitespace=True：自动去除字符串字段的前后空格。
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

#search_requirements 的输入
#模型调用search_requirements 时，只能传这些字段
class SearchRequirementsInput(ToolModel):
    query: str = Field(min_length=1, max_length=500)#查询文本
    modules: list[str] = Field(default_factory=list, max_length=10)#按功能模块筛选
    statuses: list[Literal["active", "archived"]] = Field(default_factory=list, max_length=2)#按状态筛选
    top_k: Annotated[int, Field(default=5, ge=1, le=10)] = 5#返回的最相关结果数量

#search_requirements 的输出
class RequirementSearchItem(ToolModel):
    requirement_key: str#需求编号,例如REQ-A5CE8140
    version_number: int#需求版本
    title: str#需求标题
    functional_modules: list[str]#功能模块
    status: str#需求状态
    relevance_score: float#混合检索得到的相关性得分
    match_reason: str#匹配原因


class SearchRequirementsOutput(ToolModel):
    items: list[RequirementSearchItem]

#get_requirement_detail 的输入
class GetRequirementDetailInput(ToolModel):
    requirement_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")

#需求版本历史
class RequirementVersionSummary(ToolModel):
    version_number: int#需求版本
    change_type: str#变更类型
    change_reason: str#变更原因
    created_at: datetime#创建时间

#需求详情输出
class RequirementDetailOutput(ToolModel):
    requirement_key: str
    current_version: int | None
    title: str
    description: str
    acceptance_criteria: list[str]
    review_status: str
    history: list[RequirementVersionSummary]
    source_record_ids: list[int]
    feature_lineage: list[dict[str, object]]

#get_source_detail 的输入
class GetSourceDetailInput(ToolModel):
    source_record_id: Annotated[int, Field(gt=0)]

#get_source_detail 的输出
class SourceDetailOutput(ToolModel):
    source_record_id: int
    source_key: str
    channel_type: str
    submitted_at: datetime
    raw_text_summary: str
    attachments: list[dict[str, object]]
    processing_status: str
    analyses: list[dict[str, object]]

#get_conversation_summary 的输入
class GetConversationSummaryInput(ToolModel):
    conversation_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")

#get_conversation_summary 的输出
class ConversationSummaryOutput(ToolModel):
    conversation_key: str
    confirmed_requirements: list[str]
    pending_questions: list[str]
    discovered_conflicts: list[str]
    recent_messages: list[dict[str, object]]

#需求报告工具，后端计算真实统计数据，模型只负责整理成报告语言。
class GetRequirementReportSnapshotInput(ToolModel):
    module: str | None = Field(default=None, min_length=1, max_length=255)
    conversation_key: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    statuses: list[Literal["active", "archived"]] = Field(default_factory=list, max_length=2)
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: Annotated[int, Field(default=5, ge=1, le=20)] = 5


class RequirementReportSnapshotOutput(ToolModel):
    scope: str
    generated_at: datetime
    requirement_counts: dict[str, object]
    review_summary: dict[str, int]
    risk_conflict_summary: dict[str, object]
    recent_changes: list[dict[str, object]]
    requirements: list[dict[str, object]]
    conversation_summary: ConversationSummaryOutput | None = None
    references: list[dict[str, str]]


class ToolError(ToolModel):
    code: str
    message: str


class ToolExecutionResult(ToolModel):
    ok: bool
    data: dict[str, object] | None = None
    error: ToolError | None = None
