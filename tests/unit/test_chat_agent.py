from typing import Any

from requirement_agent.ai.llm.base import ChatStreamChunk, ToolCall, ToolChatResponse
from requirement_agent.ai.skills import get_skill
from requirement_agent.ai.tools.schemas import SearchRequirementsOutput
from requirement_agent.application.chat_agent import ChatAgentService, _official_result_urls


class DummySession:
    def add(self, value: object) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


class FakeChat:
    def __init__(self, responses: list[ToolChatResponse], *, unsupported: bool = False) -> None:
        self.responses = responses
        self.unsupported = unsupported
        self.calls = 0
        self.messages: list[list[dict[str, object]]] = []

    async def complete_with_tools(self, messages: list[dict[str, object]], *, tools: list[dict[str, object]]) -> ToolChatResponse:
        self.calls += 1
        self.messages.append(messages)
        if self.unsupported:
            raise NotImplementedError
        return self.responses.pop(0)

    async def complete(self, messages: list[dict[str, str]], *, analysis_type: str | None = None) -> str:
        return "普通文本降级回答"


class StreamingChat(FakeChat):
    def __init__(self, responses: list[ToolChatResponse], chunks: list[str]) -> None:
        super().__init__(responses)
        self.chunks = chunks
        self.timeline: list[str] = []

    async def stream_with_tools(self, messages: list[dict[str, object]], *, tools: list[dict[str, object]]):
        assert tools == []  # Final user-visible turn must not re-enter the tool loop.
        for index, chunk in enumerate(self.chunks):
            self.timeline.append(f"model-{index}")
            yield ChatStreamChunk(content=chunk)
        self.timeline.append("model-finished")
        yield ChatStreamChunk(finish_reason="stop")


class FakeTools:
    async def search_requirements(self, value: Any) -> SearchRequirementsOutput:
        return SearchRequirementsOutput(items=[])

    async def tavily_search(self, value: Any) -> dict[str, object]:
        return {"results": [{"title": "AI News", "url": "https://example.test/news"}]}


class FailingTools:
    async def search_requirements(self, value: Any) -> SearchRequirementsOutput:
        raise RuntimeError("database unavailable")


class VerifiedWebTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def tavily_search(self, value: Any) -> dict[str, object]:
        self.calls.append(("tavily_search", value.model_dump()))
        return {
            "results": [
                {"url": "https://docs.example.test/release"},
                {"url": "https://github.com/example/project/releases"},
                {"url": "https://pypi.org/project/example"},
            ]
        }

    async def tavily_extract(self, value: Any) -> dict[str, object]:
        self.calls.append(("tavily_extract", value.model_dump()))
        return {
            "results": [
                {
                    "title": "Example release",
                    "url": "https://github.com/example/project/releases",
                    "raw_content": "example==2.0.0",
                }
            ]
        }


def _agent(chat: FakeChat) -> ChatAgentService:
    return ChatAgentService(DummySession(), chat, FakeTools(), "user-1", "CONV-1")  # type: ignore[arg-type]


async def test_agent_returns_direct_model_answer_without_tool() -> None:
    answer, tools, references = await _agent(FakeChat([ToolChatResponse(content="直接回答")])).answer("你好")
    assert (answer, tools, references) == ("直接回答", [], [])


async def test_streamed_final_answer_reaches_downstream_before_model_finishes() -> None:
    chat = StreamingChat([ToolChatResponse(content="规划阶段不应显示")], ["第一段", "第二段"])
    observed: list[str] = []

    async def on_delta(value: str) -> None:
        observed.append(value)
        chat.timeline.append(f"downstream-{value}")

    async def on_tool(_: str, __: str, ___: str) -> None:
        raise AssertionError("this request has no tool call")

    answer, _, _ = await _agent(chat).answer_stream("解释 RAG", on_delta=on_delta, on_tool=on_tool)
    assert answer == "第一段第二段"
    assert observed == ["第一段", "第二段"]
    assert chat.timeline.index("downstream-第一段") < chat.timeline.index("model-finished")
    assert "规划阶段不应显示" not in "".join(observed)


async def test_tool_planning_is_hidden_but_final_answer_streams() -> None:
    chat = StreamingChat(
        [
            ToolChatResponse(tool_calls=[ToolCall(id="1", name="search_requirements", arguments='{"query":"RAG"}')]),
            ToolChatResponse(content="无需额外工具"),
        ],
        ["检索后", "回答"],
    )
    deltas: list[str] = []
    tools: list[tuple[str, str]] = []

    async def on_delta(value: str) -> None:
        deltas.append(value)

    async def on_tool(phase: str, name: str, _: str) -> None:
        tools.append((phase, name))

    answer, summaries, _ = await _agent(chat).answer_stream("查 RAG", on_delta=on_delta, on_tool=on_tool)
    assert answer == "检索后回答"
    assert deltas == ["检索后", "回答"]
    assert tools == [("start", "search_requirements"), ("end", "search_requirements")]
    assert summaries[0]["tool_name"] == "search_requirements"


async def test_agent_executes_a_valid_tool_then_generates_answer() -> None:
    chat = FakeChat([ToolChatResponse(tool_calls=[ToolCall(id="1", name="search_requirements", arguments='{"query":"歌词"}')]), ToolChatResponse(content="找到 REQ-1")])
    answer, calls, _ = await _agent(chat).answer("查歌词")
    assert answer == "找到 REQ-1"
    assert calls == [
        {
            "tool_name": "search_requirements",
            "ok": True,
            "summary": "已返回 0 条结果",
            "references": [],
        }
    ]


async def test_agent_rejects_unknown_tool_without_crashing() -> None:
    chat = FakeChat([ToolChatResponse(tool_calls=[ToolCall(id="1", name="delete_everything", arguments="{}")]), ToolChatResponse(content="不能执行")])
    answer, calls, _ = await _agent(chat).answer("删除")
    assert answer == "不能执行"
    assert calls[0]["ok"] is False


async def test_agent_returns_safe_result_for_invalid_tool_arguments() -> None:
    chat = FakeChat(
        [
            ToolChatResponse(
                tool_calls=[
                    ToolCall(id="1", name="search_requirements", arguments='{"top_k":999}')
                ]
            ),
            ToolChatResponse(content="请补充查询内容"),
        ]
    )
    answer, calls, _ = await _agent(chat).answer("查")
    assert answer == "请补充查询内容"
    assert calls[0]["ok"] is False


async def test_agent_returns_safe_result_when_tool_fails() -> None:
    chat = FakeChat(
        [
            ToolChatResponse(
                tool_calls=[
                    ToolCall(id="1", name="search_requirements", arguments='{"query":"歌词"}')
                ]
            ),
            ToolChatResponse(content="查询暂不可用"),
        ]
    )
    agent = ChatAgentService(DummySession(), chat, FailingTools(), "user-1", "CONV-1")  # type: ignore[arg-type]
    answer, calls, _ = await agent.answer("查")
    assert answer == "查询暂不可用"
    assert calls[0]["ok"] is False


async def test_agent_stops_after_three_tool_rounds() -> None:
    call = ToolChatResponse(tool_calls=[ToolCall(id="1", name="search_requirements", arguments='{"query":"a"}')])
    chat = FakeChat([call, call, call, ToolChatResponse(content="不应执行")])
    answer, calls, _ = await _agent(chat).answer("a")
    assert "最大工具调用次数" in answer
    assert len(calls) == 3


async def test_verified_web_fact_forces_extract_after_search() -> None:
    chat = FakeChat([
        ToolChatResponse(tool_calls=[ToolCall(
            id="search-1", name="tavily_search", arguments='{"query":"example latest version"}'
        )]),
        ToolChatResponse(content="根据官方正文，版本为 2.0.0。"),
    ])
    tools = VerifiedWebTools()
    agent = ChatAgentService(
        DummySession(), chat, tools, "user-1", "CONV-1", get_skill("general_assistant")
    )  # type: ignore[arg-type]

    answer, calls, _ = await agent.answer(
        "example 当前最新版本是多少？", force_tavily_extract_after_search=True
    )

    assert answer == "根据官方正文，版本为 2.0.0。"
    assert [call["tool_name"] for call in calls] == ["tavily_search", "tavily_extract"]
    assert [name for name, _ in tools.calls] == ["tavily_search", "tavily_extract"]
    assert tools.calls[1][1]["urls"] == [
        "https://pypi.org/project/example",
        "https://github.com/example/project/releases",
        "https://docs.example.test/release",
    ]
    assert chat.messages[1][-2]["tool_calls"][0]["function"]["name"] == "tavily_extract"


def test_verified_web_urls_prioritize_registries_releases_and_docs() -> None:
    assert _official_result_urls({"results": [
        {"url": "https://example.test/release"},
        {"url": "https://docs.example.test/versions"},
        {"url": "https://github.com/example/project/releases"},
        {"url": "https://pypi.org/project/example"},
    ]}) == [
        "https://pypi.org/project/example",
        "https://github.com/example/project/releases",
        "https://docs.example.test/versions",
    ]


def test_verified_web_urls_reject_unverifiable_sites() -> None:
    assert _official_result_urls({"results": [
        {"url": "https://example.test/version"},
        {"url": "https://medium.example.test/version"},
    ]}) == []


async def test_agent_degrades_when_provider_has_no_tool_support() -> None:
    answer, calls, _ = await _agent(FakeChat([], unsupported=True)).answer("查历史")
    assert answer == "普通文本降级回答"
    assert calls == []


async def test_reporting_skill_rejects_tools_outside_its_read_only_whitelist() -> None:
    chat = FakeChat([ToolChatResponse(tool_calls=[
        ToolCall(id="1", name="search_requirements", arguments='{"query":"x"}'),
    ]), ToolChatResponse(content="无未授权查询")])
    agent = ChatAgentService(DummySession(), chat, FakeTools(), "user-1", "CONV-1", get_skill("requirement_reporting"))  # type: ignore[arg-type]
    _, calls, _ = await agent.answer("生成需求报告")
    assert calls[0]["ok"] is False
    assert "search_requirements" not in get_skill("requirement_reporting").allowed_tools


async def test_general_skill_keeps_tavily_urls_as_web_references_and_history() -> None:
    chat = FakeChat([
        ToolChatResponse(tool_calls=[ToolCall(
            id="1", name="tavily_search", arguments='{"query":"AI news", "topic":"general"}'
        )]),
        ToolChatResponse(content="这是今天的 AI 新闻。"),
    ])
    agent = ChatAgentService(
        DummySession(), chat, FakeTools(), "user-1", "CONV-1", get_skill("general_assistant")
    )  # type: ignore[arg-type]
    answer, _, references = await agent.answer(
        "今天有哪些 AI 新闻？", history=[{"role": "user", "content": "LangGraph 是什么？"}]
    )
    assert answer == "这是今天的 AI 新闻。"
    assert references == [{
        "type": "web", "id": "https://example.test/news", "url": "https://example.test/news", "title": "AI News"
    }]
    assert chat.messages[0][1] == {"role": "user", "content": "LangGraph 是什么？"}
    assert chat.responses == []
