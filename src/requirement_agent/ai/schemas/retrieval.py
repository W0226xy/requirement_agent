from typing import Annotated

from pydantic import Field

from requirement_agent.ai.schemas.common import StrictAIModel


class CandidateFeature(StrictAIModel):
    feature_key: str
    module: str
    feature_title: str
    feature_description: str
    acceptance_criteria: list[str]


class CandidateSource(StrictAIModel):
    source_key: str
    channel_type: str
    submitter_name: str
    evidence_text: str


class RequirementCandidate(StrictAIModel):
    requirement_key: str
    version_number: Annotated[int, Field(gt=0)]
    title: str
    functional_modules: list[str]
    features: list[CandidateFeature]
    similarity_score: Annotated[float, Field(ge=0, le=1)]
    matched_text: str
    sources: list[CandidateSource]

