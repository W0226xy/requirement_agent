"""LLM-backed, schema-validated routing for unified conversations."""
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from requirement_agent.ai.llm.base import ChatModel
from requirement_agent.shared.errors import LLMServiceError


class ConversationIntent(StrEnum):
    REQUIREMENT_SUBMISSION = "requirement_submission"
    TRACEABILITY_QUERY = "traceability_query"
    REPORTING_QUERY = "reporting_query"
    CLARIFICATION = "clarification"
    GENERAL_QUERY = "general_query"


class IntentClassification(BaseModel):
    """The classifier's constrained output; no natural-language label parsing."""

    model_config = ConfigDict(extra="forbid")

    intent: ConversationIntent
    confidence: float = Field(ge=0, le=1)
    requires_web_search: bool


_QUERY_MARKERS = ("有没有", "查询", "历史需求", "来源", "之前怎么改", "之前修改", "req-")
_SUBMISSION_MARKERS = (
    "新增", "需要", "支持", "功能", "优化", "修改", "实现", "需求",
    "add ", "modify ", "delete ", "restore ", "requirement", "feature",
)
_REPORTING_MARKERS = ("需求报告", "报告", "总结", "汇总", "概述", "统计", "进展", "风险汇总", "当前会话")
_WEB_MARKERS = (
    "最新", "新闻", "实时", "今天", "当前版本", "current version", "latest", "today", "news",
)
_CLARIFICATION_MARKERS = ("是的", "不是", "补充", "刚才", "上一条", "前面说的")

_CLASSIFIER_PROMPT = """你是需求管理系统的路由分类器。只根据当前消息和有限会话上下文分类，绝不调用检索或假设系统内数据。
可选 intent：
- requirement_submission：新增、修改、删除、恢复需求或带附件的需求输入。
- traceability_query：查询历史需求、来源、版本或修改记录。
- reporting_query：需求总结、报告、统计、进展或风险汇总。
- clarification：回答上一轮“需求澄清”问题的补充。
- general_query：与需求管理无关的普通问答。
requires_web_search 只对 general_query 有意义；新闻、今天、最新、实时、当前版本等易变信息为 true。稳定知识和一般解释为 false。
严格按给定 JSON Schema 返回，不要解释。"""


class IntentClassifier:
    """Uses the existing LLM adapter's Pydantic structured-output capability."""

    def __init__(self, model: ChatModel) -> None:
        self._model = model

    async def classify(
        self,
        message: str,
        *,
        recent_context: list[dict[str, str]],
        has_attachment: bool,
    ) -> IntentClassification:
        if has_attachment:
            return IntentClassification(
                intent=ConversationIntent.REQUIREMENT_SUBMISSION,
                confidence=1,
                requires_web_search=False,
            )
        messages = [
            {"role": "system", "content": _CLASSIFIER_PROMPT},
            {
                "role": "user",
                "content": _classification_input(message, recent_context),
            },
        ]
        try:
            result = await self._model.complete_structured(
                messages, schema=IntentClassification, temperature=0.1
            )
            # Never allow a malformed model policy to send requirement traffic to web.
            return result.model_copy(
                update={
                    "requires_web_search": (
                        result.requires_web_search
                        if result.intent == ConversationIntent.GENERAL_QUERY
                        else False
                    )
                }
            )
        except (LLMServiceError, ValueError, NotImplementedError):
            return fallback_classify_conversation_message(
                message, has_attachment=has_attachment
            )


def _classification_input(message: str, recent_context: list[dict[str, str]]) -> str:
    context = recent_context[-6:]
    rendered = "\n".join(
        f"{item.get('role', 'user')}: {item.get('content', '')[:800]}" for item in context
    )
    return f"最近会话上下文（可能为空）：\n{rendered or '（无）'}\n\n当前消息：\n{message}"


def fallback_classify_conversation_message(
    text: str, *, has_attachment: bool
) -> IntentClassification:
    """Conservative availability fallback when structured LLM classification fails."""
    normalized = text.strip().lower()
    if has_attachment:
        intent = ConversationIntent.REQUIREMENT_SUBMISSION
    elif any(marker in normalized for marker in _REPORTING_MARKERS):
        intent = ConversationIntent.REPORTING_QUERY
    elif any(marker in normalized for marker in _QUERY_MARKERS) and "需求" in normalized:
        intent = ConversationIntent.TRACEABILITY_QUERY
    elif any(marker in normalized for marker in _SUBMISSION_MARKERS):
        intent = ConversationIntent.REQUIREMENT_SUBMISSION
    elif any(marker in normalized for marker in _CLARIFICATION_MARKERS):
        intent = ConversationIntent.CLARIFICATION
    else:
        intent = ConversationIntent.GENERAL_QUERY
    return IntentClassification(
        intent=intent,
        confidence=0.35,
        requires_web_search=(
            intent == ConversationIntent.GENERAL_QUERY
            and any(marker in normalized for marker in _WEB_MARKERS)
        ),
    )


def classify_conversation_message(text: str, *, has_attachment: bool) -> ConversationIntent:
    """Backward-compatible synchronous keyword fallback for legacy callers."""
    return fallback_classify_conversation_message(
        text, has_attachment=has_attachment
    ).intent
