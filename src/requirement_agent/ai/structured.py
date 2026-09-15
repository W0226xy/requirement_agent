import logging
from collections.abc import Callable
from time import perf_counter
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from requirement_agent.ai.llm.base import ChatModel
from requirement_agent.infrastructure.database.models import AnalysisResult
from requirement_agent.shared.enums import AnalysisType
from requirement_agent.shared.errors import LLMServiceError, StructuredOutputError

logger = logging.getLogger(__name__)

#泛型：支持不同的输出 Schema
#StructuredLLM 不绑定某一种结果结构，只要是 Pydantic BaseModel 子类都可以使用。
SchemaT = TypeVar("SchemaT", bound=BaseModel)
#schema=RequirementExtraction用于需求提取
#schema=ConflictAnalysis用于冲突分析

#Prompt → LLM 原始文本
      # → Pydantic JSON 校验
      # → 业务规则校验
      # → 成功：返回结构化对象
      # → 失败：记录审计 + 让模型按报错自动修正
      # → 多次仍失败：抛出 StructuredOutputError

class StructuredLLM:
    def __init__(
        self,
        model: ChatModel,#聊天模型，实际负责调用 OpenAI 兼容接口
        session: AsyncSession,#异步数据库会话，用于写 AI 调用审计记录
        *,
        max_retries: int,#结构化输出失败后的最大自动修正次数。，默认 2 次
    ) -> None:
        self._model = model
        self._session = session
        self._max_retries = max_retries

    async def generate(
        self,
        *,
        source_record_id: int,#本次分析属于哪条原始需求
        analysis_type: AnalysisType,#分析类型，需求提取、冲突分析等
        prompt_version: str,#Prompt 版本号，用于审计记录
        schema: type[SchemaT],#Pydantic 模型类，用于校验 LLM 输出
        messages: list[dict[str, str]],#发给 LLM 的 system/user 消息
        input_snapshot: dict[str, object],#输入快照，用于审计记录
        result_validator: Callable[[SchemaT], None] | None = None,#可选的额外校验函数，用于在 Pydantic 校验后进一步验证结果
    ) -> SchemaT:#返回的是经过校验的 Pydantic 对象，而不是不可靠的字符串。
        current_messages = list(messages)
        last_error = "structured generation failed"
        for attempt in range(1, self._max_retries + 2):#总共尝试次数 = 最大重试次数 + 1（第一次尝试）
            started = perf_counter()
            raw_output: str | None = None
            try:
                logger.info(
                    "structured_llm_request analysis_type=%s model_name=%s attempt=%s "
                    "message_count=%s input_chars=%s",
                    analysis_type.value,
                    self._model.model_name,
                    attempt,
                    len(current_messages),
                    sum(len(message["content"]) for message in current_messages),
                )
                #调用 LLM 生成原始输出
                raw_output = await self._model.complete(
                    current_messages,
                    analysis_type=analysis_type.value,
                )
                #将原始输出解析为 Pydantic 对象,并进行校验(比如字段是否完整、类型是否正确、格式是否符合要求等)
                result = schema.model_validate_json(raw_output)
                #额外业务校验，比如某些字段的值是否在允许范围内，或者某些字段之间的关系是否符合业务规则等
                #例如冲突分析中，模型可能返回了一个并不在检索候选集中的历史需求 ID。JSON 格式可能正确，但业务上不允许。
                if result_validator is not None:
                    result_validator(result)
            except (ValidationError, TypeError, ValueError) as exc:#Pydantic 校验失败或额外校验失败
                last_error = f"{type(exc).__name__}: {exc}"
                await self._record(#记录本次失败的结果到数据库
                    source_record_id=source_record_id,
                    analysis_type=analysis_type,
                    prompt_version=prompt_version,
                    input_snapshot=input_snapshot,
                    raw_output=raw_output,
                    result=None,
                    duration_ms=self._duration_ms(started),
                    error_message=last_error,
                    attempt_number=attempt,
                )
                if attempt <= self._max_retries:#如果还没超过最大重试次数，则让模型根据报错信息自动修正输出
                    current_messages.append(
                        {
                            "role": "assistant",
                            "content": raw_output or "",
                        }
                    )
                    current_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "The previous JSON failed validation. Correct it, preserve the "
                                "source language, and return "
                                f"only valid JSON. Validation error: {last_error}"
                            ),
                        }
                    )
                    continue
                raise StructuredOutputError(
                    f"model output remained invalid after {attempt} attempts"
                ) from exc
            except LLMServiceError as exc:#LLM 服务调用失败，比如网络错误、超时、模型不可用等
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "structured_llm_service_error analysis_type=%s model_name=%s "
                    "attempt=%s error_type=%s duration_ms=%s",
                    analysis_type.value,
                    self._model.model_name,
                    attempt,
                    type(exc).__name__,
                    self._duration_ms(started),
                )
                await self._record(
                    source_record_id=source_record_id,
                    analysis_type=analysis_type,
                    prompt_version=prompt_version,
                    input_snapshot=input_snapshot,
                    raw_output=None,
                    result=None,
                    duration_ms=self._duration_ms(started),
                    error_message=last_error,
                    attempt_number=attempt,
                )
                raise
            else:
                await self._record(#记录本次成功的结果到数据库
                    source_record_id=source_record_id,
                    analysis_type=analysis_type,
                    prompt_version=prompt_version,
                    input_snapshot=input_snapshot,
                    raw_output=raw_output,
                    result=result,
                    duration_ms=self._duration_ms(started),
                    error_message=None,
                    attempt_number=attempt,
                )
                return result
        raise StructuredOutputError(last_error)

    #每一次成功、失败、自动纠错都会插入一条 AnalysisResult
    async def _record(#记录本次分析结果到数据库
        self,
        *,
        source_record_id: int,#本次分析属于哪条原始需求
        analysis_type: AnalysisType,#分析类型，需求提取、冲突分析等
        prompt_version: str,#Prompt 版本号，用于审计记录
        input_snapshot: dict[str, object],#输入快照，用于审计记录
        raw_output: str | None,#LLM 原始输出，用于审计记录
        result: BaseModel | None,#经过 Pydantic 校验的结构化结果对象，用于审计记录
        duration_ms: int,#本次分析耗时，单位毫秒，用于审计记录
        error_message: str | None,#本次分析的错误信息，用于审计记录
        attempt_number: int,#本次分析的尝试次数，用于审计记录
    ) -> None:
        self._session.add(
            AnalysisResult(
                source_record_id=source_record_id,
                analysis_type=analysis_type,
                model_name=self._model.model_name,
                prompt_version=prompt_version,
                input_snapshot=input_snapshot,
                result_json=result.model_dump(mode="json") if result is not None else None,
                raw_output=raw_output,
                confidence=None,
                duration_ms=duration_ms,
                error_message=error_message,
                attempt_number=attempt_number,
            )
        )
        await self._session.commit()

    #计算从 started 到现在的时间差，单位毫秒
    @staticmethod
    def _duration_ms(started: float) -> int:
        return max(0, round((perf_counter() - started) * 1000))
