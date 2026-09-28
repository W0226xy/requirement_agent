"""General assistant built on the project's existing Skill and tool-call loop."""
from requirement_agent.ai.llm.base import ChatModel
from requirement_agent.ai.skills import get_skill
from requirement_agent.ai.tools.schemas import TavilyExtractInput, TavilySearchInput
from requirement_agent.application.chat_agent import ChatAgentService
from requirement_agent.infrastructure.tavily_mcp import TavilyMCPClient


class TavilyToolService:
    def __init__(self, client: TavilyMCPClient, *, timeout_seconds: float) -> None:
        self._client = client
        self.tool_timeout_seconds = timeout_seconds

    async def tavily_search(self, value: TavilySearchInput) -> dict[str, object]:
        return await self._client.call_tool("tavily_search", value.model_dump())

    async def tavily_extract(self, value: TavilyExtractInput) -> dict[str, object]:
        return await self._client.call_tool("tavily_extract", value.model_dump())


class GeneralAssistantService:
    def __init__(
        self,
        chat_model: ChatModel,
        tavily: TavilyMCPClient,
        *,
        conversation_key: str,
        tool_timeout_seconds: float,
    ) -> None:
        self._agent = ChatAgentService(
            session=None,
            chat_model=chat_model,
            tool_service=TavilyToolService(tavily, timeout_seconds=tool_timeout_seconds),
            actor_id="general-assistant",
            conversation_key=conversation_key,
            skill=get_skill("general_assistant"),
            audit_tools=False,
        )

    async def answer(
        self,
        user_message: str,
        *,
        history: list[dict[str, str]],
        requires_web_search: bool,
    ) -> tuple[str, list[dict[str, object]], list[dict[str, object]]]:
        instruction = (
            "此问题涉及易变的实时信息：必须先调用 tavily_search；"
            "若搜索失败，明确说明联网不可用后再作答。"
            if requires_web_search
            else "先判断是否真的需要联网；稳定知识不要为了联网而调用工具。"
        )
        return await self._agent.answer(
            user_message, history=history, additional_system_instruction=instruction
        )
