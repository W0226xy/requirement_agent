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
#这段代码定义了项目中 Agent 输出的结构化数据协议：
#不让大模型随意输出一段自然语言，而是要求它严格返回符合字段、类型、长度和业务规则的 JSON。
#RequirementExtraction   需求提取节点的输出
#ConflictAnalysis        冲突/风险分析节点的输出

class RequirementEntities(StrictAIModel):
    platform: str | None#所属平台
    page: str | None#涉及页面
    target: str | None#操作对象
    actors: list[str]#使用者/参与者

#RequirementExtraction是工作流中 extract 节点要求大模型返回的结构。
class RequirementExtraction(StrictAIModel):
    #简短的需求摘要
    requirement_summary: Annotated[str, Field(min_length=1, max_length=500)]
    #完整、标准化的需求描述
    requirement_description: Annotated[str, Field(min_length=1, max_length=20_000)]
    #需求所属功能模块
    functional_modules: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=255)]],
        Field(min_length=1, max_length=3),
    ]
    #可验证的验收条件
    acceptance_criteria: list[str]
    #信息不明确时需要确认的问题
    clarification_questions: list[str]
    #需求中的实体信息
    entities: RequirementEntities


class FeatureContent(StrictAIModel):#用于描述“一个可以被新增、修改或恢复的功能点”。
    module: Annotated[str, Field(min_length=1, max_length=255)]#建议的功能模块名称，可能与现有模块不同
    feature_title: Annotated[str, Field(min_length=1, max_length=500)]##功能点标题
    feature_description: Annotated[str, Field(min_length=1, max_length=10_000)]#功能描述
    acceptance_criteria: list[str]#验收标准
#例如：{
#   "module": "地图导航",
#   "feature_title": "返回车辆位置导航",
#   "feature_description": "用户点击首页停车记录卡片后，系统应打开地图并以停车位置作为导航目的地。",
#   "acceptance_criteria": [
#     "点击停车记录卡片后可成功进入导航",
#     "导航目的地应为最近一次停车位置"
#   ]
# }后续审核通过后，这些内容可能会被写入正式需求版本中的 RequirementFeature


# ProposedOperation 用于描述“一个针对需求冲突或风险的建议操作”，包括操作类型、涉及的功能点、来源需求 ID 和操作理由。
class ProposedOperation(StrictAIModel):
    operation: ChangeOperation #ADD、MODIFY、DELETE、RESTORE
    feature_key: str | None #用于标识现有功能点的唯一键，可能是数据库中的主键或其他唯一标识符
    content: FeatureContent | None #新增/修改后的完整功能内容
    source_record_id: Annotated[int | None, Field(gt=0)] = None #本次建议来源于哪条原始需求
    reason: Annotated[str, Field(min_length=1, max_length=2_000)] #建议操作的理由或说明

    #在 Pydantic 完成字段解析后，再检查字段组合是否符合业务规则。
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


# RequirementConflict 用于描述“一个需求冲突”，包括冲突的类型、涉及的需求 ID、冲突描述、证据和置信度。
class RequirementConflict(StrictAIModel):
    requirement_id: Annotated[str, Field(min_length=1, max_length=64)]
    type: ConflictType
    description: Annotated[str, Field(min_length=1, max_length=5_000)]
    evidence: Annotated[str, Field(min_length=1, max_length=5_000)]
    confidence: Annotated[float, Field(ge=0, le=1)]

# RequirementRisk 用于描述“一个需求风险”，包括风险的类型、等级、描述、概率、影响和缓解措施。
class RequirementRisk(StrictAIModel):
    type: Annotated[str, Field(min_length=1, max_length=128)]
    level: RiskLevel
    description: Annotated[str, Field(min_length=1, max_length=5_000)]
    probability: ProbabilityLevel
    impact: RiskLevel
    mitigation: Annotated[str, Field(min_length=1, max_length=5_000)]

# ConflictAnalysis 用于描述“一个需求冲突/风险分析”，包括冲突状态、相关需求 ID、冲突列表、风险列表、建议操作列表和澄清问题列表。
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

