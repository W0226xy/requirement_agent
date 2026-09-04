from collections.abc import Callable
from time import perf_counter
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from requirement_agent.ai.llm.base import ChatModel
from requirement_agent.infrastructure.database.models import AnalysisResult
from requirement_agent.shared.enums import AnalysisType
from requirement_agent.shared.errors import LLMServiceError, StructuredOutputError

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class StructuredLLM:
    def __init__(
        self,
        model: ChatModel,
        session: AsyncSession,
        *,
        max_retries: int,
    ) -> None:
        self._model = model
        self._session = session
        self._max_retries = max_retries

    async def generate(
        self,
        *,
        source_record_id: int,
        analysis_type: AnalysisType,
        prompt_version: str,
        schema: type[SchemaT],
        messages: list[dict[str, str]],
        input_snapshot: dict[str, object],
        result_validator: Callable[[SchemaT], None] | None = None,
    ) -> SchemaT:
        current_messages = list(messages)
        last_error = "structured generation failed"
        for attempt in range(1, self._max_retries + 2):
            started = perf_counter()
            raw_output: str | None = None
            try:
                raw_output = await self._model.complete(current_messages)
                result = schema.model_validate_json(raw_output)
                if result_validator is not None:
                    result_validator(result)
            except (ValidationError, TypeError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                await self._record(
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
                if attempt <= self._max_retries:
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
            except LLMServiceError as exc:
                last_error = str(exc)
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
                await self._record(
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

    async def _record(
        self,
        *,
        source_record_id: int,
        analysis_type: AnalysisType,
        prompt_version: str,
        input_snapshot: dict[str, object],
        raw_output: str | None,
        result: BaseModel | None,
        duration_ms: int,
        error_message: str | None,
        attempt_number: int,
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

    @staticmethod
    def _duration_ms(started: float) -> int:
        return max(0, round((perf_counter() - started) * 1000))
