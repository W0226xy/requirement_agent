"""Bounded orchestration for the read-only conversational Agent."""
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from time import perf_counter

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from requirement_agent.ai.llm.base import ChatModel
from requirement_agent.ai.skills import AgentSkill, get_skill
from requirement_agent.ai.tools import TOOL_BY_NAME, openai_tools
from requirement_agent.ai.tools.schemas import ToolExecutionResult
from requirement_agent.infrastructure.database.models import AuditLog
from requirement_agent.shared.enums import AuditActionType, AuditEntityType
from requirement_agent.shared.errors import LLMServiceError

logger = logging.getLogger(__name__)
MAX_TOOL_ROUNDS = 3
TOOL_TIMEOUT_SECONDS = 15


class ChatAgentService:
    #初始化：绑定当前会话、用户和 Skill
    def __init__(self, session: AsyncSession | None, chat_model: ChatModel, tool_service: object,
                 actor_id: str, conversation_key: str, skill: AgentSkill | None = None,
                 audit_tools: bool = True) -> None:
        self._session, self._chat_model, self._tools = session, chat_model, tool_service#数据库会话
        self._actor_id, self._conversation_key = actor_id, conversation_key#当前用户，用于权限和审计
        self._skill = skill or get_skill()#当前任务的 Skill；
        self._audit_tools = audit_tools

    async def answer(
        self,
        user_message: str,
        *,
        history: list[dict[str, str]] | None = None,
        additional_system_instruction: str = "",
        force_tavily_extract_after_search: bool = False,
    ) -> tuple[str, list[dict[str, object]], list[dict[str, object]]]:
        messages: list[dict[str, object]] = [
            #一开始构造两条消息system_prompt、output_constraints和当前会话的 conversation_key，作为系统消息；
            # 然后把用户消息作为用户消息
            {"role": "system", "content": f"{self._skill.system_prompt}\n{self._skill.output_constraints}\n{additional_system_instruction}\n当前会话 conversation_key：{self._conversation_key}"},
        ]
        messages.extend(
            {"role": item["role"], "content": item["content"]}
            for item in history or []
        )
        messages.append({"role": "user", "content": user_message})
        summaries: list[dict[str, object]] = []
        try:
            for _ in range(MAX_TOOL_ROUNDS):#最多3轮工具调用，超过三轮仍未回答，就返回
                #只暴露当前 Skill 允许的工具
                response = await self._chat_model.complete_with_tools(
                    messages, tools=openai_tools(self._skill.allowed_tools)
                )
                #模型不调用工具时，直接返回回答
                #通常发生在模型已经拿到工具结果后，生成最终回答，返回内容包括：
                #回答文本、工具调用摘要、工具调用结果引用等
                if not response.tool_calls:
                    return response.content or "未能生成回答。", summaries, _references(summaries)
                #模型调用了工具时，记录工具调用摘要和结果引用
                messages.append({"role": "assistant", "content": response.content or "",
                    "tool_calls": [{"id": call.id, "type": "function", "function":
                        {"name": call.name, "arguments": call.arguments}} for call in response.tool_calls]})
                #执行工具调用，记录结果摘要和引用
                for call in response.tool_calls:
                    result = await self._execute(call.name, call.arguments)
                    summaries.append(
                        {
                            "tool_name": call.name,
                            "ok": result.ok,
                            "summary": _result_summary(result),
                            "references": _result_references(result),
                        }
                    )
                    #把工具调用结果作为工具消息，继续下一轮对话
                    messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                        "content": result.model_dump_json()})
                    if (
                        force_tavily_extract_after_search
                        and call.name == "tavily_search"
                        and result.ok
                        and not any(item.name == "tavily_extract" for item in response.tool_calls)
                    ):
                        extract_result = await self._force_tavily_extract(messages, result)
                        if extract_result is None:
                            return (
                                "暂时无法确认：搜索结果中没有可验证的官方页面。",
                                summaries,
                                _references(summaries),
                            )
                        summaries.append(
                            {
                                "tool_name": "tavily_extract",
                                "ok": extract_result.ok,
                                "summary": _result_summary(extract_result),
                                "references": _result_references(extract_result),
                            }
                        )
                        if not extract_result.ok:
                            return (
                                "暂时无法确认：无法读取官方页面以验证该实时事实。",
                                summaries,
                                _references(summaries),
                            )
            return "已达到本次查询的最大工具调用次数，请缩小问题范围后重试。", summaries, _references(summaries)
        except (LLMServiceError, NotImplementedError) as exc:
            logger.info("chat_tool_call_unavailable conversation=%s error_type=%s", self._conversation_key, type(exc).__name__)
            # Preserve availability for providers without tool support.  This is intentionally plain text.
            fallback_messages = [{"role": "system", "content": self._skill.system_prompt}]
            fallback_messages.extend(history or [])
            fallback_messages.append({"role": "user", "content": user_message})
            return await self._chat_model.complete(
                fallback_messages, analysis_type="conversation_summary"
            ), summaries, _references(summaries)

    async def answer_stream(
        self, user_message: str, *, history: list[dict[str, str]] | None = None,
        on_delta: Callable[[str], Awaitable[None]],
        on_tool: Callable[[str, str, str], Awaitable[None]],
        additional_system_instruction: str = "",
    ) -> tuple[str, list[dict[str, object]], list[dict[str, object]]]:
        """Tool loop with true provider streaming only for the final visible turn."""
        if not hasattr(self._chat_model, "stream_with_tools"):
            logger.info("chat_stream_degraded provider=%s", self._chat_model.model_name)
            return await self.answer(user_message, history=history)
        messages: list[dict[str, object]] = [{"role": "system", "content":
            f"{self._skill.system_prompt}\n{self._skill.output_constraints}\n"
            f"{additional_system_instruction}\n"
            "当前阶段只决定是否调用工具；如无需工具，不要回答用户问题。\n"
            f"当前会话 conversation_key：{self._conversation_key}"}]
        messages.extend({"role": item["role"], "content": item["content"]} for item in history or [])
        messages.append({"role": "user", "content": user_message})
        summaries: list[dict[str, object]] = []
        for _ in range(MAX_TOOL_ROUNDS):
            # Planning is deliberately non-streaming: its text and JSON are never
            # user-visible. Once evidence is ready, the separate final turn below
            # streams immediately without exposing tools.
            response = await self._chat_model.complete_with_tools(
                messages, tools=openai_tools(self._skill.allowed_tools)
            )
            calls = response.tool_calls
            if not calls:
                return await self._stream_final_answer(messages, summaries, on_delta)
            messages.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}
                for call in calls]})
            for call in calls:
                await on_tool("start", call.name, "正在查询相关资料")
                result = await self._execute(call.name, call.arguments)
                summary = _result_summary(result)
                summaries.append({"tool_name": call.name, "ok": result.ok, "summary": summary,
                                  "references": _result_references(result)})
                await on_tool("end", call.name, summary)
                messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                 "content": result.model_dump_json()})
        return "已达到本次查询的最大工具调用次数，请缩小问题范围后重试。", summaries, _references(summaries)

    async def _stream_final_answer(
        self,
        messages: list[dict[str, object]],
        summaries: list[dict[str, object]],
        on_delta: Callable[[str], Awaitable[None]],
    ) -> tuple[str, list[dict[str, object]], list[dict[str, object]]]:
        """Generate the only user-visible answer; emit each provider chunk now."""
        final_messages = [*messages, {"role": "system", "content":
            "现在基于已有上下文和工具结果直接回答用户。禁止调用任何工具。"}]
        parts: list[str] = []
        async for chunk in self._chat_model.stream_with_tools(final_messages, tools=[]):  # type: ignore[attr-defined]
            if chunk.content:
                parts.append(chunk.content)
                await on_delta(chunk.content)
            if chunk.tool_calls:
                # A compliant provider cannot return tools when none were offered.
                raise LLMServiceError("model attempted tools in final streaming turn")
        answer = "".join(parts)
        if not answer:
            raise LLMServiceError("model stream ended without final content")
        return answer, summaries, _references(summaries)

    #Function Call 最关键的保护部分。
    async def _execute(self, name: str, raw_arguments: str) -> ToolExecutionResult:
        started = perf_counter()
        safe_parameters: dict[str, object] = {}
        result: ToolExecutionResult | None = None
        try:
            #根据工具名查找工具定义，并检查当前 Skill 是否允许调用该工具
            definition = TOOL_BY_NAME.get(name)
            if definition is None or name not in self._skill.allowed_tools:
                result = ToolExecutionResult(ok=False, error={"code": "unknown_tool", "message": "不允许调用该工具"})
                return result
            try:
                parsed = json.loads(raw_arguments)
                if not isinstance(parsed, dict): raise ValueError("arguments must be object")
                value = definition.input_model.model_validate(parsed)
                safe_parameters = _redact(value.model_dump())
            except (json.JSONDecodeError, ValidationError, ValueError):
                result = ToolExecutionResult(ok=False, error={"code": "invalid_arguments", "message": "工具参数不合法"})
                return result
            method = getattr(self._tools, name)
            timeout_seconds = getattr(self._tools, "tool_timeout_seconds", TOOL_TIMEOUT_SECONDS)
            data = await asyncio.wait_for(method(value), timeout=timeout_seconds)
            if data is None:
                result = ToolExecutionResult(ok=False, error={"code": "not_found", "message": "未找到该业务数据或无权访问"})
                return result
            serialized = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
            if not isinstance(serialized, Mapping):
                raise TypeError("tool result must be a mapping")
            result = ToolExecutionResult(ok=True, data=dict(serialized))
            return result
        except TimeoutError:
            result = ToolExecutionResult(ok=False, error={"code": "timeout", "message": "查询超时，请缩小范围后重试"})
            return result
        except Exception as exc:
            logger.exception(
                "chat_tool_execution_failed tool=%s exception_type=%s "
                "exception_message=%s mcp_status_code=%s mcp_response=%r",
                name,
                type(exc).__name__,
                str(exc)[:1_000],
                getattr(exc, "status_code", None),
                getattr(exc, "response_body", None),
            )
            result = ToolExecutionResult(ok=False, error={"code": "tool_failed", "message": "查询暂时不可用"})
            return result
        finally:
            await self._audit(name, safe_parameters, started, result)

    async def _audit(self, name: str, parameters: dict[str, object], started: float,
                     result: ToolExecutionResult | None) -> None:
        if not self._audit_tools or self._session is None:
            return
        try:
            self._session.add(AuditLog(
                actor_id=self._actor_id,
                action_type=AuditActionType.AGENT_TOOL_CALLED,
                entity_type=AuditEntityType.CONVERSATION,
                entity_id=self._conversation_key,
                before_data=None,
                after_data={"tool_name": name, "parameters": parameters,
                    "duration_ms": round((perf_counter() - started) * 1000),
                    "success": result.ok if result else False,
                    "error_code": result.error.code if result and result.error else None},
            ))
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            logger.exception("chat_tool_audit_failed tool=%s", name)

    async def _force_tavily_extract(
        self, messages: list[dict[str, object]], search_result: ToolExecutionResult
    ) -> ToolExecutionResult | None:
        urls = _official_result_urls(search_result.data or {})
        if not urls:
            return None
        call_id = "forced_tavily_extract"
        arguments = json.dumps({"urls": urls, "extract_depth": "basic"})
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": "tavily_extract", "arguments": arguments},
                    }
                ],
            }
        )
        result = await self._execute("tavily_extract", arguments)
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": "tavily_extract",
                "content": result.model_dump_json(),
            }
        )
        return result


def _redact(value: dict[str, object]) -> dict[str, object]:
    return {key: (f"{str(item)[:80]}…" if key == "query" and len(str(item)) > 80 else item)
            for key, item in value.items()}


def _official_result_urls(data: Mapping[str, object]) -> list[str]:
    """Prefer package registries, release pages, then official documentation."""
    results = data.get("results")
    if not isinstance(results, list):
        return []
    candidates = [
        item["url"]
        for item in results
        if isinstance(item, Mapping)
        and isinstance(item.get("url"), str)
        and _official_url_priority(item["url"])[0] < 4
    ]
    return sorted(dict.fromkeys(candidates), key=_official_url_priority)[:3]


def _official_url_priority(url: str) -> tuple[int, str]:
    lowered = url.lower()
    if "pypi.org/" in lowered or "npmjs.com/" in lowered:
        return 0, lowered
    if "github.com/" in lowered and "/releases" in lowered:
        return 1, lowered
    if "docs." in lowered or "/docs/" in lowered:
        return 2, lowered
    if "langchain.com/" in lowered:
        return 3, lowered
    return 4, lowered


def _result_summary(result: ToolExecutionResult) -> str:
    if not result.ok: return result.error.message if result.error else "查询失败"
    data = result.data or {}
    items = data.get("items")
    return f"已返回 {len(items)} 条结果" if isinstance(items, list) else "查询完成"


def _references(summaries: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[str, str]] = set()
    references: list[dict[str, object]] = []
    for summary in summaries:
        values = summary.get("references", [])
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            kind, identifier = value.get("type"), value.get("id")
            if not isinstance(kind, str) or not isinstance(identifier, str):
                continue
            if (kind, identifier) not in seen:
                seen.add((kind, identifier))
                references.append(value)
    return references


def _result_references(result: ToolExecutionResult) -> list[dict[str, object]]:
    if not result.ok or result.data is None:
        return []
    data = result.data
    references: list[dict[str, object]] = []
    requirement_key = data.get("requirement_key")
    if isinstance(requirement_key, str):
        references.append({"type": "requirement", "id": requirement_key})
    source_id = data.get("source_record_id")
    if isinstance(source_id, int):
        references.append({"type": "source", "id": str(source_id)})
    items = data.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("requirement_key"), str):
                references.append({"type": "requirement", "id": item["requirement_key"]})
    source_record_ids = data.get("source_record_ids")
    for source_id in source_record_ids if isinstance(source_record_ids, list) else []:
        if isinstance(source_id, int):
            references.append({"type": "source", "id": str(source_id)})
    stored_references = data.get("references")
    for reference in stored_references if isinstance(stored_references, list) else []:
        if isinstance(reference, dict) and isinstance(reference.get("type"), str) and isinstance(reference.get("id"), str):
            references.append({"type": reference["type"], "id": reference["id"]})
    results = data.get("results")
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        reference: dict[str, object] = {"type": "web", "id": item["url"], "url": item["url"]}
        if isinstance(item.get("title"), str):
            reference["title"] = item["title"]
        references.append(reference)
    return references
