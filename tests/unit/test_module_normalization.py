import pytest
from pydantic import BaseModel

from requirement_agent.ai.language import validate_output_language
from requirement_agent.ai.modules import normalize_modules, normalize_operation_module
from requirement_agent.shared.enums import AnalysisType


class LanguageResult(BaseModel):
    requirement_summary: str
    requirement_description: str


def test_source_mentioned_existing_module_wins_over_new_synonym() -> None:
    result = normalize_modules(
        "增加音乐播放器倍速功能",
        ["播放控制模块"],
        ["音乐播放器", "座椅按摩"],
    )

    assert result == ["音乐播放器"]


def test_module_suffix_alias_reuses_existing_module() -> None:
    result = normalize_operation_module(
        "Improve controls",
        "播放控制模块",
        ["播放控制"],
    )

    assert result == "播放控制"


def test_chinese_source_rejects_english_extraction() -> None:
    with pytest.raises(ValueError, match="must be Chinese"):
        validate_output_language(
            "音乐播放器增加快进功能",
            LanguageResult(
                requirement_summary="Add fast forward",
                requirement_description="Add fast forward to the music player.",
            ),
            AnalysisType.EXTRACTION,
        )


def test_chinese_source_accepts_chinese_extraction() -> None:
    validate_output_language(
        "音乐播放器增加快进功能",
        LanguageResult(
            requirement_summary="增加快进功能",
            requirement_description="为音乐播放器增加快进功能。",
        ),
        AnalysisType.EXTRACTION,
    )
