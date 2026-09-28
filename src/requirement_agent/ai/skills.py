"""Project-internal Agent skills; this is deliberately unrelated to Codex skills."""
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSkill:
    name: str#skill名称
    description: str#用途说明
    allowed_tools: frozenset[str]#允许使用的工具
    system_prompt: str#系统提示
    output_constraints: str#输出约束

#frozenset 是不可修改的集合
_TRACEABILITY_TOOLS = frozenset({"search_requirements", "get_requirement_detail", "get_source_detail", "get_conversation_summary"})
_REPORTING_TOOLS = frozenset({"get_requirement_report_snapshot", "get_conversation_summary", "get_requirement_detail"})
_GENERAL_TOOLS = frozenset({"tavily_search", "tavily_extract"})
SKILLS = {#SKILLS 是所有 Skill 的注册表
    #历史需求、来源和会话信息
    "requirement_traceability": AgentSkill(
        name="requirement_traceability", description="查询需求、来源及会话追溯信息", allowed_tools=_TRACEABILITY_TOOLS,
        system_prompt="你是需求追溯助手。涉及历史数据必须优先调用工具，不能编造编号、状态或来源。",
        output_constraints="用中文简洁回答；只引用工具实际返回的 REQ 或 SourceRecord 编号。",
    ),
    #需求质量评估和报告
    "requirement_quality_review": AgentSkill("requirement_quality_review", "评估需求质量", frozenset(), "仅提出质量建议。", "不得执行写操作。"),
    #需求报告生成
    "requirement_reporting": AgentSkill(
        name="requirement_reporting",
        description="基于当前用户可访问的真实需求、审核、风险和会话数据生成中文需求报告",
        allowed_tools=_REPORTING_TOOLS,
        system_prompt=(
            "你是需求报告助手。必须优先调用 get_requirement_report_snapshot；用户问当前会话时必须传入当前会话 "
            "conversation_key。只能依据工具返回的数据写报告，不能编造统计数字、需求状态、风险、来源或 REQ 编号。"
        ),
        output_constraints=(
            "使用中文和以下 Markdown 标题：## 需求概述、## 需求状态、## 核心需求、## 风险与待确认事项、"
            "## 最近变更与建议。概述 2～4 句，核心需求不超过 5 条，建议不超过 3 条；无真实风险或冲突时明确写“当前范围内未发现已记录的风险或冲突”。"
            "所有数值、需求编号和来源引用只能来自工具结果，不得执行写操作。"
        ),
    ),
    "general_assistant": AgentSkill(
        name="general_assistant",
        description="回答不属于需求管理的通用问题，可按需联网",
        allowed_tools=_GENERAL_TOOLS,
        system_prompt=(
            "你是通用问答助手。优先使用会话上下文回答稳定知识；只有问题需要最新、新闻、实时或网页事实时才调用 Tavily MCP。"
            "使用搜索结果时，只依据工具返回内容作答，不编造来源或链接。"
        ),
        output_constraints=(
            "用中文简洁、准确地回答。联网结果的来源 URL 由系统保存为引用；工具失败时说明无法联网后继续基于已有知识回答。"
            "一次 tavily_search 或 tavily_extract 成功后，应直接根据结果作答，除非用户明确要求继续查找其他网页。"
        ),
    ),
}


def get_skill(name: str = "requirement_traceability") -> AgentSkill:
    return SKILLS[name]
