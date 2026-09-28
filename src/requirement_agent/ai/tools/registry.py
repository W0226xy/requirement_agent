"""The small, explicit read-only tool registry for the chat agent."""
from dataclasses import dataclass

from pydantic import BaseModel

from requirement_agent.ai.tools.schemas import (
    GetConversationSummaryInput, GetRequirementDetailInput, GetSourceDetailInput,
    GetRequirementReportSnapshotInput, SearchRequirementsInput, TavilyExtractInput,
    TavilySearchInput,
)


@dataclass(frozen=True)
class ToolDefinition:#每个工具的说明模板
    name: str#工具名称
    description: str#工具描述
    input_model: type[BaseModel]#Pydantic 参数模型，负责约束和校验传入参数

    #把项目里的工具定义转换成 OpenAI Function Call 所需格式
    def openai_schema(self) -> dict[str, object]:
        return {"type": "function", "function": {"name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema()}}




TOOLS = (
    #按关键词、模块等查历史正式需求
    ToolDefinition("search_requirements", "查询已入库历史需求；涉及历史需求时优先调用。", SearchRequirementsInput),
    #根据需求编号查询当前内容、历史版本和来源追溯
    ToolDefinition("get_requirement_detail", "根据需求编号查询当前内容、历史版本和来源追溯。", GetRequirementDetailInput),
    #根据 SourceRecord 数字 ID 查询来源及安全摘要
    ToolDefinition("get_source_detail", "根据 SourceRecord 数字 ID 查询来源及安全摘要。", GetSourceDetailInput),
    #查询当前用户有权访问会话的摘要和最近消息概览
    ToolDefinition("get_conversation_summary", "查询当前用户有权访问会话的摘要和最近消息概览。", GetConversationSummaryInput),
    #按当前用户权限计算需求报告所需的真实统计、审核、风险、变更和引用；不写入数据
    ToolDefinition("get_requirement_report_snapshot", "按当前用户权限计算需求报告所需的真实统计、审核、风险、变更和引用；不写入数据。", GetRequirementReportSnapshotInput),
    ToolDefinition("tavily_search", "通过 Tavily MCP 搜索公开网页；新闻、最新、实时问题优先调用。", TavilySearchInput),
    ToolDefinition("tavily_extract", "通过 Tavily MCP 提取已知网页 URL 的正文；先搜索再按需提取。", TavilyExtractInput),
)
TOOL_BY_NAME = {item.name: item for item in TOOLS}#是按工具名快速查找的字典


#Skill 和 Function Call 连接的关键。它不会把全部工具都交给模型，而是只返回当前 Skill 允许调用的工具。
def openai_tools(allowed_names: frozenset[str]) -> list[dict[str, object]]:
    return [item.openai_schema() for item in TOOLS if item.name in allowed_names]
