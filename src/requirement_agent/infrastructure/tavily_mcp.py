"""A small client for Tavily's hosted MCP server, never its HTTP API."""
import json
import logging
from collections.abc import Mapping
from typing import Any

from requirement_agent.shared.config import Settings

logger = logging.getLogger(__name__)

# Tavily MCP 客户端错误
class TavilyMCPError(RuntimeError):
    """Raised for an unavailable or invalid MCP result; callers degrade safely."""

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,#失败调用阶段，例如 "connect"、"initialize"、"list_tools"、"call_tool"
        status_code: int | None = None,#HTTP 状态码，例如 401、403、500
        response_body: str | None = None,#HTTP 响应体的前 2,000 个字符，避免泄露敏感信息
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.status_code = status_code
        self.response_body = response_body


class TavilyMCPClient:
    def __init__(self, settings: Settings) -> None:
        self._enabled = settings.tavily_mcp_enabled#是否启用 Tavily MCP
        self._url = settings.tavily_mcp_url#Tavily MCP 的 URL，例如 "https://mcp.tavily.com"
        self._api_key = settings.tavily_api_key.get_secret_value()#Tavily MCP 的 API Key，用于身份验证
        self._timeout = settings.tavily_mcp_timeout_seconds#工具调用超时，单位秒

    async def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        if not self._enabled or not self._api_key:#如果未启用 Tavily MCP 或未配置 API Key，则抛出错误
            raise TavilyMCPError("Tavily MCP is not configured", stage="configuration")
        try:
            from mcp import ClientSession  # type: ignore[import-not-found]
            from mcp.client.streamable_http import (  # type: ignore[import-not-found]
                streamablehttp_client,
            )
        except ImportError as exc:
            raise TavilyMCPError(
                "MCP client dependency is unavailable", stage="dependency"
            ) from exc
        headers = {"Authorization": f"Bearer {self._api_key}"}
        stage = "connect"#建立 HTTP/MCP 通道
        try:
            #进入 MCP 协议通信
            async with streamablehttp_client(#建立的是 MCP 的 Streamable HTTP 通信通道；它返回读流和写流。
                self._url,
                headers=headers,#HTTP 请求头，包含身份验证信息
                timeout=self._timeout,#请求超时
                sse_read_timeout=self._timeout,#SSE 读取超时
            ) as streams:
                read_stream, write_stream, *_ = streams
                async with ClientSession(read_stream, write_stream) as session:
                    stage = "initialize"#与远端 MCP Server 先握手、协商协议和能力。
                    await session.initialize()
                    stage = "list_tools"#列出远端Tavily MCP Server 可用的工具列表。
                    listed = await session.list_tools()
                    remote_name = _remote_tool_name(name, listed.tools)
                    stage = "call_tool"#调用远端 Tavily MCP Server 的指定工具，并传递参数。
                    result = await session.call_tool(remote_name, arguments)
        except Exception as exc:#捕获所有异常，记录日志并抛出 TavilyMCPError
            status_code, response_body = _response_details(exc)#提取 HTTP 响应状态码和响应体的前 2,000 个字符，避免泄露敏感信息
            logger.exception(#记录诊断日志
                "tavily_mcp_call_failed tool=%s stage=%s exception_type=%s "
                "exception_message=%s http_status=%s response_body=%r",
                name,
                stage,
                type(exc).__name__,
                str(exc)[:1_000],
                status_code,
                response_body,
            )
            raise TavilyMCPError(
                "Tavily MCP request failed",
                stage=stage,
                status_code=status_code,
                response_body=response_body,
            ) from exc
        if getattr(result, "isError", False):
            response_body = _safe_result_text(result)
            logger.warning(
                "tavily_mcp_tool_error tool=%s response_body=%r", name, response_body
            )
            raise TavilyMCPError(
                "Tavily MCP tool returned an error",
                stage="call_tool",
                response_body=response_body,
            )
        return _normalize_result(getattr(result, "content", []))

#兼容工具名风格，例如本地工具名为 "my_tool"，远端 MCP 工具名为 "my-tool" 或 "my_tool"，都能匹配。
def _remote_tool_name(name: str, tools: list[Any]) -> str:
    variants = {name, name.replace("_", "-"), name.replace("-", "_")}
    for tool in tools:
        candidate = getattr(tool, "name", "")
        if candidate in variants:
            return candidate
    raise TavilyMCPError(f"Tavily MCP does not expose {name}")

#把 MCP 内容统一为字典
def _normalize_result(content: list[Any]) -> dict[str, object]:
    """Normalize MCP content while retaining result URLs verbatim."""
    values: list[object] = []
    for item in content:
        dumped = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        if isinstance(dumped, Mapping) and isinstance(dumped.get("text"), str):
            try:
                values.append(json.loads(dumped["text"]))
            except json.JSONDecodeError:
                values.append({"content": dumped["text"]})
        else:
            values.append(dumped)
    payload: object = values[0] if len(values) == 1 else values
    if isinstance(payload, Mapping):
        mapped = dict(payload)
        results = mapped.get("results")
        if isinstance(results, list):
            return {"results": [_safe_result(item) for item in results]}
        return mapped
    return {"results": [{"content": json.dumps(payload, ensure_ascii=False)}]}

#搜索结果白名单化
def _safe_result(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {"content": str(value)}
    return {
        key: value[key]
        for key in ("title", "url", "content", "raw_content", "score", "published_date")
        if key in value
    }

#从异常中有限提取 HTTP 信息
def _response_details(exc: BaseException) -> tuple[int | None, str | None]:
    """Extract only bounded HTTP diagnostics; never log request headers or API keys."""
    for item in _exception_tree(exc):
        response = getattr(item, "response", None)
        status_code = getattr(response, "status_code", None)
        if not isinstance(status_code, int):
            continue
        body = getattr(response, "text", "")
        return status_code, str(body)[:2_000] or None
    return None, None

#支持异常组
def _exception_tree(exc: BaseException) -> list[BaseException]:
    children = getattr(exc, "exceptions", None)
    if isinstance(children, tuple):
        return [leaf for child in children for leaf in _exception_tree(child)]
    return [exc]

#错误结果长度限制
def _safe_result_text(result: object) -> str:
    if hasattr(result, "model_dump_json"):
        return str(result.model_dump_json())[:2_000]
    return str(result)[:2_000]
