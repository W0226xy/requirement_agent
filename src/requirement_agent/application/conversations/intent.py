"""Small deterministic routing guard for the unified conversation endpoint."""
from enum import StrEnum


class ConversationIntent(StrEnum):
    REQUIREMENT_SUBMISSION = "requirement_submission"#新需求录入，走原有 SourceRecord + Celery + AI 分析。
    TRACEABILITY_QUERY = "traceability_query"#查询历史需求，使用 requirement_traceability skill。
    REPORTING_QUERY = "reporting_query"#需求报告查询，使用 requirement_reporting skill。
    CLARIFICATION = "clarification"#简短补充或澄清，不新建需求来源。


_QUERY_MARKERS = ("有没有", "查询", "历史需求", "来源", "之前怎么改", "之前修改", "req-")
_SUBMISSION_MARKERS = ("新增", "需要", "支持", "功能", "优化", "修改", "实现", "需求")
_REPORTING_MARKERS = ("需求报告", "报告", "汇总", "概述", "统计", "进展", "风险汇总", "当前会话")


def classify_conversation_message(text: str, *, has_attachment: bool) -> ConversationIntent:
    normalized = text.strip().lower()
    if has_attachment:
        return ConversationIntent.REQUIREMENT_SUBMISSION
    if any(marker in normalized for marker in _REPORTING_MARKERS):
        return ConversationIntent.REPORTING_QUERY
    if any(marker in normalized for marker in _QUERY_MARKERS) or normalized.endswith(("?", "？")):
        return ConversationIntent.TRACEABILITY_QUERY
    if any(marker in normalized for marker in _SUBMISSION_MARKERS) or len(normalized) > 6:
        return ConversationIntent.REQUIREMENT_SUBMISSION
    return ConversationIntent.CLARIFICATION
