import pytest
from pydantic import ValidationError

from requirement_agent.ai.tools.schemas import TavilySearchInput
from requirement_agent.infrastructure.tavily_mcp import _normalize_result


class TextBlock:
    def model_dump(self, mode: str) -> dict[str, str]:
        assert mode == "json"
        return {
            "type": "text",
            "text": (
                '{"results": [{"title": "Tavily", "url": "https://example.test/a", '
                '"content": "ok"}]}'
            ),
        }


def test_mcp_result_normalization_preserves_source_url() -> None:
    result = _normalize_result([TextBlock()])
    assert result["results"] == [{
        "title": "Tavily", "url": "https://example.test/a", "content": "ok"
    }]


def test_search_input_matches_remote_mcp_general_topic_schema() -> None:
    value = TavilySearchInput(
        query="latest AI news", time_range="day", include_domains=["example.com"]
    )
    assert value.topic == "general"
    assert value.time_range == "day"
    with pytest.raises(ValidationError):
        TavilySearchInput(query="latest AI news", topic="news")  # type: ignore[arg-type]
