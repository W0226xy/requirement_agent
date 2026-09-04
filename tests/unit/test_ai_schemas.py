import json

import pytest
from pydantic import ValidationError

from requirement_agent.ai.schemas.analysis import ConflictAnalysis, RequirementExtraction


def extraction_payload() -> dict[str, object]:
    return {
        "requirement_summary": "Export reports",
        "requirement_description": "Users can export reports as PDF.",
        "functional_modules": ["reporting"],
        "acceptance_criteria": ["The exported file is a valid PDF."],
        "clarification_questions": [],
        "entities": {
            "platform": "web",
            "page": "reports",
            "target": "report",
            "actors": ["analyst"],
        },
    }


def conflict_payload() -> dict[str, object]:
    return {
        "conflict_status": "none",
        "related_requirement_ids": [],
        "conflicts": [],
        "risks": [],
        "proposed_operations": [
            {
                "operation": "add",
                "feature_key": None,
                "content": {
                    "module": "reporting",
                    "feature_title": "Export PDF",
                    "feature_description": "Export the current report.",
                    "acceptance_criteria": ["A PDF is downloaded."],
                },
                "source_record_id": 1,
                "reason": "New capability",
            }
        ],
        "clarification_questions": [],
    }


def test_extraction_rejects_null_arrays() -> None:
    payload = extraction_payload()
    payload["functional_modules"] = None

    with pytest.raises(ValidationError):
        RequirementExtraction.model_validate_json(json.dumps(payload))


def test_conflict_status_is_a_fixed_enum() -> None:
    payload = conflict_payload()
    payload["conflict_status"] = "probably_duplicate"

    with pytest.raises(ValidationError):
        ConflictAnalysis.model_validate_json(json.dumps(payload))


def test_unknown_fields_are_rejected() -> None:
    payload = extraction_payload()
    payload["invented"] = "value"

    with pytest.raises(ValidationError):
        RequirementExtraction.model_validate_json(json.dumps(payload))


def test_delete_operation_rejects_content() -> None:
    payload = conflict_payload()
    operation = payload["proposed_operations"][0]  # type: ignore[index]
    operation["operation"] = "delete"  # type: ignore[index]
    operation["feature_key"] = "FEAT-001"  # type: ignore[index]

    with pytest.raises(ValidationError, match="delete requires"):
        ConflictAnalysis.model_validate_json(json.dumps(payload))
