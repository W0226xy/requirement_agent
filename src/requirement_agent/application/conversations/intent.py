"""LLM-backed, schema-validated routing for unified conversations."""
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from requirement_agent.ai.llm.base import ChatModel
from requirement_agent.shared.errors import LLMServiceError


class ConversationIntent(StrEnum):
    REQUIREMENT_SUBMISSION = "requirement_submission"#提交、修改、删除或恢复需求
    TRACEABILITY_QUERY = "traceability_query"#查询历史需求、来源、版本
    REPORTING_QUERY = "reporting_query"#需求汇总、统计、报告
    CLARIFICATION = "clarification"#对上一轮需求澄清问题的补充
    GENERAL_QUERY = "general_query"#与需求系统无关的普通问答


#模型必须遵守的返回结构，这里使用 Pydantic 做结构化约束。
class IntentClassification(BaseModel):
    """The classifier's constrained output; no natural-language label parsing."""

    model_config = ConfigDict(extra="forbid")

    intent: ConversationIntent#最终路由类别，必须是前述五种之一
    confidence: float = Field(ge=0, le=1)#模型对判断结果可信度
    requires_web_search: bool#是否需要联网搜索。

#没有 LLM 时的规则分类关键词
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

#分类任务的规则说明
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
        message: str,#当前用户输入
        *,
        recent_context: list[dict[str, str]],#最近六条会话上下文，包含 role 和 content
        has_attachment: bool,#当前消息是否带附件
    ) -> IntentClassification:
        if has_attachment:#带附件的消息不交给 LLM 判断，而是直接归为需求提交
            #PDF、Word、图片等附件在你的系统中默认被认为是待分析的需求材料
            return IntentClassification(
                intent=ConversationIntent.REQUIREMENT_SUBMISSION,
                confidence=1,
                requires_web_search=False,
            )
        messages = [#构建 LLM 消息
            #系统消息，告诉模型它的角色和任务
            {"role": "system", "content": _CLASSIFIER_PROMPT},
            #用户消息，包含当前消息和最近六条会话上下文
            {
                "role": "user",
                "content": _classification_input(message, recent_context),
            },
        ]
        try:
            #调用 LLM 的 structured 输出接口，要求返回 IntentClassification 结构化对象
            result = await self._model.complete_structured(
                messages, schema=IntentClassification, temperature=0.1
            )
            # Never allow a malformed model policy to send requirement traffic to web.
            return result.model_copy(
                update={
                    "requires_web_search": (
                        result.requires_web_search
                        if result.intent == ConversationIntent.GENERAL_QUERY#只有与需求无关的普通问答才可能需要联网搜索
                        else False
                    )
                }
            )
        except (LLMServiceError, ValueError, NotImplementedError):
            return fallback_classify_conversation_message(
                message, has_attachment=has_attachment
            )

#_classification_input() 会取最近六条会话，并把每条内容截断到 800 字符
#作用是解决指代和补充问题，模型能结合上下文判断它是 CLARIFICATION，而不是一条新的需求
def _classification_input(message: str, recent_context: list[dict[str, str]]) -> str:
    context = recent_context[-6:]
    rendered = "\n".join(
        f"{item.get('role', 'user')}: {item.get('content', '')[:800]}" for item in context
    )
    return f"最近会话上下文（可能为空）：\n{rendered or '（无）'}\n\n当前消息：\n{message}"

#无 LLM 时的规则分类
def fallback_classify_conversation_message(
    text: str, *, has_attachment: bool
) -> IntentClassification:
    """Conservative availability fallback when structured LLM classification fails."""
    normalized = text.strip().lower()#将文本标准化为小写，去除首尾空白
    if has_attachment:#带附件的消息直接归为需求提交
        intent = ConversationIntent.REQUIREMENT_SUBMISSION
    elif any(marker in normalized for marker in _REPORTING_MARKERS):#包含需求报告、总结、汇总等关键词的消息归为需求汇总查询
        intent = ConversationIntent.REPORTING_QUERY
    elif any(marker in normalized for marker in _QUERY_MARKERS) and "需求" in normalized:#包含查询、历史需求、来源等关键词且包含“需求”的消息归为需求追溯查询
        intent = ConversationIntent.TRACEABILITY_QUERY
    elif any(marker in normalized for marker in _SUBMISSION_MARKERS):#包含提交、上传等关键词的消息归为需求提交
        intent = ConversationIntent.REQUIREMENT_SUBMISSION
    elif any(marker in normalized for marker in _CLARIFICATION_MARKERS):#包含澄清、确认等关键词的消息归为需求澄清
        intent = ConversationIntent.CLARIFICATION
    else:
        intent = ConversationIntent.GENERAL_QUERY#不符合上述条件的消息归为普通问答
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
