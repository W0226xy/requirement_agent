import pytest

from requirement_agent.application.conversations.intent import (
    ConversationIntent,
    IntentClassification,
    IntentClassifier,
    fallback_classify_conversation_message,
)
from requirement_agent.shared.errors import LLMServiceError


class StructuredClassifierModel:
    def __init__(self, result: IntentClassification | Exception) -> None:
        self._result = result
        self.messages: list[dict[str, str]] = []

    async def complete_structured(
        self,
        messages: list[dict[str, str]],
        *,
        schema: type[IntentClassification],
        temperature: float = 0.1,
    ) -> IntentClassification:
        self.messages = messages
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.mark.parametrize(
    ("message", "intent", "web"),
    [
        ("新增停车位置管理功能", ConversationIntent.REQUIREMENT_SUBMISSION, False),
        ("之前有没有停车位置相关需求？", ConversationIntent.TRACEABILITY_QUERY, False),
        ("总结当前需求", ConversationIntent.REPORTING_QUERY, False),
        ("PostgreSQL GIN 是什么？", ConversationIntent.GENERAL_QUERY, False),
        ("今天有哪些 AI 新闻？", ConversationIntent.GENERAL_QUERY, True),
    ],
)
async def test_llm_classifier_returns_pydantic_intent(
    message: str, intent: ConversationIntent, web: bool
) -> None:
    model = StructuredClassifierModel(IntentClassification(
        intent=intent, confidence=0.97, requires_web_search=web
    ))
    classification = await IntentClassifier(model).classify(  # type: ignore[arg-type]
        message,
        recent_context=[{"role": "user", "content": "上一个普通问题"}],
        has_attachment=False,
    )
    assert classification.intent == intent
    assert classification.requires_web_search is web
    assert model.messages[0]["role"] == "system"
    assert "上一个普通问题" in model.messages[1]["content"]
    assert model.messages[1]["content"].endswith(message)


async def test_non_general_web_flag_is_safely_ignored() -> None:
    classifier = IntentClassifier(StructuredClassifierModel(IntentClassification(
        intent=ConversationIntent.REPORTING_QUERY, confidence=0.9, requires_web_search=True
    )))  # type: ignore[arg-type]
    result = await classifier.classify("总结当前需求", recent_context=[], has_attachment=False)
    assert result.requires_web_search is False


async def test_classifier_falls_back_when_structured_llm_is_unavailable() -> None:
    classifier = IntentClassifier(StructuredClassifierModel(LLMServiceError("offline")))  # type: ignore[arg-type]
    result = await classifier.classify(
        "PostgreSQL GIN 是什么？", recent_context=[], has_attachment=False
    )
    assert result.intent == ConversationIntent.GENERAL_QUERY


def test_keyword_fallback_keeps_reporting_behavior() -> None:
    assert fallback_classify_conversation_message(
        "生成当前需求库的需求报告", has_attachment=False
    ).intent == ConversationIntent.REPORTING_QUERY
