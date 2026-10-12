"""General assistant built on the project's existing Skill and tool-call loop."""
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from requirement_agent.ai.llm.base import ChatModel
from requirement_agent.ai.skills import get_skill
from requirement_agent.ai.tools.schemas import TavilyExtractInput, TavilySearchInput
from requirement_agent.application.chat_agent import ChatAgentService
from requirement_agent.infrastructure.tavily_mcp import TavilyMCPClient

_BEIJING_TIMEZONE = ZoneInfo("Asia/Shanghai")
_DATE_QUESTION = re.compile(
    r"(?:今天|明天|昨天).{0,12}(?:几月几号|几号|日期|星期几|周几|星期)",
)
_TIME_QUESTION = re.compile(
    r"(?:现在|当前|此刻).{0,8}(?:几点|时间)|(?:北京时间|中国时间).{0,8}(?:几点|时间)",
)
_VERIFIED_WEB_FACT = re.compile(
    r"(?:当前|最新|实时|现价|现在).{0,16}(?:版本|release|发布|价格|报价|状态)"
    r"|(?:版本|release|发布|价格|报价|状态).{0,16}(?:当前|最新|实时|现在)",
    re.IGNORECASE,
)


def _current_datetime_answer(
    user_message: str, *, now: datetime | None = None
) -> str | None:
    """Answer unambiguous local clock questions without a search index or an LLM."""
    if not (_DATE_QUESTION.search(user_message) or _TIME_QUESTION.search(user_message)):
        return None

    beijing_now = (now or datetime.now(_BEIJING_TIMEZONE)).astimezone(_BEIJING_TIMEZONE)
    if "明天" in user_message:
        target_date = beijing_now.date() + timedelta(days=1)
        prefix = "按北京时间，明天是"
    elif "昨天" in user_message:
        target_date = beijing_now.date() - timedelta(days=1)
        prefix = "按北京时间，昨天是"
    elif _DATE_QUESTION.search(user_message):
        target_date = beijing_now.date()
        prefix = "当前北京时间为"
    else:
        return f"当前北京时间为 {beijing_now:%Y 年 %-m 月 %-d 日 %H:%M:%S}。"

    weekday = "一二三四五六日"[target_date.weekday()]
    return f"{prefix} {target_date:%Y 年 %-m 月 %-d 日}，星期{weekday}。"


def _runtime_time_instruction(now: datetime) -> str:
    beijing_now = now.astimezone(_BEIJING_TIMEZONE)
    return (
        "运行时事实（最高优先级）：当前北京时间为 "
        f"{beijing_now.isoformat(timespec='seconds')}（Asia/Shanghai，UTC+08:00）。"
        "涉及今天、昨天、明天、当前、现在或最新等相对时间时，必须以此为准；"
        "不得使用模型记忆、历史会话、搜索摘要、文章发布日期或网页更新时间替代该时间。"
    )

#把已有的 ChatAgentService 配置成一个“只允许使用 Tavily 搜索工具的通用助手”
#如果普通问题被判定为需要联网搜索，则必须先调用 tavily_search；如果搜索失败，则明确说明联网不可用后再作答。

class TavilyToolService:
    def __init__(self, client: TavilyMCPClient, *, timeout_seconds: float) -> None:
        self._client = client
        self.tool_timeout_seconds = timeout_seconds
    #把 Tavily MCP 封装成 Agent 可调用的两个工具
    #
    async def tavily_search(self, value: TavilySearchInput) -> dict[str, object]:
        return await self._client.call_tool("tavily_search", value.model_dump())
    #用于提取已知网页的正文，例如模型先搜索，再决定读取其中一两个页面的详细内容
    async def tavily_extract(self, value: TavilyExtractInput) -> dict[str, object]:
        return await self._client.call_tool("tavily_extract", value.model_dump())

#组装一个受限 Agent
class GeneralAssistantService:
    def __init__(
        self,
        chat_model: ChatModel,#LLM，用于判断是否调用工具并生成最终回答
        tavily: TavilyMCPClient,#Tavily MCP 客户端，用于调用搜索和提取工具
        *,
        conversation_key: str,#唯一标识一个会话的 key，用于在多轮对话中保持上下文
        tool_timeout_seconds: float,#工具调用超时，单位秒
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._now_provider = now_provider or (lambda: datetime.now(_BEIJING_TIMEZONE))
        self._agent = ChatAgentService(
            session=None,#不需要数据库会话，因为我们不存储任何数据
            chat_model=chat_model,#LLM，用于判断是否调用工具并生成最终回答
            tool_service=TavilyToolService(tavily, timeout_seconds=tool_timeout_seconds),#封装了 Tavily MCP 的搜索和提取工具
            actor_id="general-assistant",#用于标识这个 Agent 的身份
            conversation_key=conversation_key,#唯一标识一个会话的 key，用于在多轮对话中保持上下文
            skill=get_skill("general_assistant"),#获取通用助手的技能定义，包含如何调用工具和生成回答的逻辑
            audit_tools=False,#不记录工具调用日志，因为我们不存储任何数据
        )

    async def answer_stream(
        self, user_message: str, *, history: list[dict[str, str]] | None,
        requires_web_search: bool, on_delta: Callable[[str], Awaitable[None]],
        on_tool: Callable[[str, str, str], Awaitable[None]],
    ) -> tuple[str, list[dict[str, object]], list[dict[str, object]]]:
        direct = _current_datetime_answer(user_message)
        if direct is not None:
            await on_delta(direct)
            return direct, [], []
        instruction = _runtime_time_instruction(self._now_provider())
        if requires_web_search:
            instruction += " 必须先使用 tavily_search 获取可验证的实时资料。"
        else:
            instruction += " 稳定知识不要为了联网而调用工具。"
        return await self._agent.answer_stream(user_message, history=history, on_delta=on_delta, on_tool=on_tool,
                                               additional_system_instruction=instruction)

    async def answer(
        self,
        user_message: str,#当前问题
        *,
        history: list[dict[str, str]],#近期会话上下文
        requires_web_search: bool,#由 intent.py 的分类器判断，表示这个问题是否显然包含时效性需求。需要联网搜索的普通问题为 true；稳定知识和一般解释为 false。
    ) -> tuple[str, list[dict[str, object]], list[dict[str, object]]]:#返回一个三元组：最终文本答案、工具调用摘要、去重后的引用
        runtime_now = self._now_provider().astimezone(_BEIJING_TIMEZONE)
        clock_answer = _current_datetime_answer(user_message, now=runtime_now)
        if clock_answer is not None:
            return clock_answer, [], []
        instruction = (
            "此问题涉及易变的实时信息：必须先调用 tavily_search；"
            "若搜索失败，明确说明联网不可用后再作答。"
            if requires_web_search
            else "先判断是否真的需要联网；稳定知识不要为了联网而调用工具。"
        )
        instruction = f"{instruction}\n{_runtime_time_instruction(runtime_now)}"
        if _VERIFIED_WEB_FACT.search(user_message):
            instruction = (
                f"{instruction}\n这是精确、易变的数字或状态事实。系统将在搜索后强制提取"
                "官方来源正文；只能依据提取正文回答。若提取失败或证据不明确，必须回答“暂时无法确认”，"
                "不得使用模型记忆或搜索摘要猜测具体值。"
            )
        return await self._agent.answer(
            user_message,
            history=history,
            additional_system_instruction=instruction,
            force_tavily_extract_after_search=bool(_VERIFIED_WEB_FACT.search(user_message)),
        )
