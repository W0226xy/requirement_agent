import json
import re

from pydantic import BaseModel

from requirement_agent.shared.enums import AnalysisType

CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")


def source_language_instruction(source_content: str) -> str:
    if contains_chinese(source_content):
        return "The source language is Chinese. All natural-language output fields must be Chinese."
    return "All natural-language output fields must use the same language as the source."


def validate_output_language(
    source_content: str,
    result: BaseModel,
    analysis_type: AnalysisType,
) -> None:
    if not contains_chinese(source_content):
        return
    payload = result.model_dump(mode="json")
    if analysis_type == AnalysisType.EXTRACTION:
        natural_text = " ".join(
            str(payload.get(field, ""))
            for field in ("requirement_summary", "requirement_description")
        )
    else:
        natural_text = json.dumps(
            {
                "conflicts": payload.get("conflicts", []),
                "risks": payload.get("risks", []),
                "clarification_questions": payload.get("clarification_questions", []),
            },
            ensure_ascii=False,
        )
        if natural_text in {
            '{"conflicts": [], "risks": [], "clarification_questions": []}',
            "",
        }:
            return
    if not contains_chinese(natural_text):
        raise ValueError("natural-language output must be Chinese for Chinese source input")


def contains_chinese(value: str) -> bool:
    return CJK_PATTERN.search(value) is not None
