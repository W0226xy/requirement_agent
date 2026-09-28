"""A small client for Tavily's hosted MCP server, never its HTTP API."""
import json
import logging
from collections.abc import Mapping
from typing import Any

from requirement_agent.shared.config import Settings

logger = logging.getLogger(__name__)


class TavilyMCPError(RuntimeError):
    """Raised for an unavailable or invalid MCP result; callers degrade safely."""

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.status_code = status_code
        self.response_body = response_body


class TavilyMCPClient:
    def __init__(self, settings: Settings) -> None:
        self._enabled = settings.tavily_mcp_enabled
        self._url = settings.tavily_mcp_url
        self._api_key = settings.tavily_api_key.get_secret_value()
        self._timeout = settings.tavily_mcp_timeout_seconds

    async def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        if not self._enabled or not self._api_key:
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
        stage = "connect"
        try:
            async with streamablehttp_client(
                self._url,
                headers=headers,
                timeout=self._timeout,
                sse_read_timeout=self._timeout,
            ) as streams:
                read_stream, write_stream, *_ = streams
                async with ClientSession(read_stream, write_stream) as session:
                    stage = "initialize"
                    await session.initialize()
                    stage = "list_tools"
                    listed = await session.list_tools()
                    remote_name = _remote_tool_name(name, listed.tools)
                    stage = "call_tool"
                    result = await session.call_tool(remote_name, arguments)
        except Exception as exc:
            status_code, response_body = _response_details(exc)
            logger.exception(
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


def _remote_tool_name(name: str, tools: list[Any]) -> str:
    variants = {name, name.replace("_", "-"), name.replace("-", "_")}
    for tool in tools:
        candidate = getattr(tool, "name", "")
        if candidate in variants:
            return candidate
    raise TavilyMCPError(f"Tavily MCP does not expose {name}")


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


def _safe_result(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {"content": str(value)}
    return {
        key: value[key]
        for key in ("title", "url", "content", "raw_content", "score")
        if key in value
    }


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


def _exception_tree(exc: BaseException) -> list[BaseException]:
    children = getattr(exc, "exceptions", None)
    if isinstance(children, tuple):
        return [leaf for child in children for leaf in _exception_tree(child)]
    return [exc]


def _safe_result_text(result: object) -> str:
    if hasattr(result, "model_dump_json"):
        return str(result.model_dump_json())[:2_000]
    return str(result)[:2_000]
