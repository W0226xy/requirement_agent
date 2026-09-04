import json
from pathlib import Path

from requirement_agent.shared.enums import ConflictStatus


def test_fixed_evaluation_dataset_covers_required_scenarios() -> None:
    path = Path(__file__).with_name("cases.json")
    cases = json.loads(path.read_text(encoding="utf-8"))

    assert len(cases) >= 8
    assert all(
        case["source_text"] and case["candidate_text"] and case["expected_evidence"]
        for case in cases
    )
    assert {case["expected"] for case in cases} == {
        status.value for status in ConflictStatus
    }
