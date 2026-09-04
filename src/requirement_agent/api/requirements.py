from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from requirement_agent.api.schemas.requirements import (
    FeatureLineageResponse,
    RequirementDiffResponse,
    RequirementFeatureResponse,
    RequirementListResponse,
    RequirementResponse,
    RequirementVersionListResponse,
    RequirementVersionResponse,
)
from requirement_agent.infrastructure.database.models import (
    FeatureLineage,
    Requirement,
    RequirementFeature,
    RequirementVersion,
)
from requirement_agent.infrastructure.database.session import get_session
from requirement_agent.shared.enums import RequirementStatus
from requirement_agent.shared.errors import RequirementNotFoundError

router = APIRouter(prefix="/api/v1/requirements", tags=["requirements"])


@router.get("", response_model=RequirementListResponse)
async def list_requirements(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: RequirementStatus | None = None,
    module: str | None = None,
    keyword: str | None = None,
    requirement_key: str | None = None,
) -> RequirementListResponse:
    filters = []
    if status is not None:
        filters.append(Requirement.status == status)
    if module is not None:
        filters.append(Requirement.functional_modules.contains([module]))
    if keyword is not None:
        filters.append(Requirement.title.ilike(f"%{keyword}%"))
    if requirement_key is not None:
        filters.append(Requirement.requirement_key == requirement_key)
    total = (
        await session.execute(select(func.count(Requirement.id)).where(*filters))
    ).scalar_one()
    requirements = (
        await session.execute(
            select(Requirement)
            .where(*filters)
            .order_by(Requirement.updated_at.desc(), Requirement.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars()
    return RequirementListResponse(
        items=[RequirementResponse.model_validate(item) for item in requirements],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{requirement_id}", response_model=RequirementResponse)
async def get_requirement(
    requirement_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RequirementResponse:
    requirement = await session.get(Requirement, requirement_id)
    if requirement is None:
        raise RequirementNotFoundError(f"requirement {requirement_id} was not found")
    features = await _current_features(session, requirement)
    lineage = await _lineage_by_feature(
        session, requirement.id, [item.feature_key for item in features]
    )
    response = RequirementResponse.model_validate(requirement)
    response.features = [
        _feature_response(item, lineage.get(item.feature_key, [])) for item in features
    ]
    return response


@router.get(
    "/{requirement_id}/versions",
    response_model=RequirementVersionListResponse,
)
async def list_requirement_versions(
    requirement_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RequirementVersionListResponse:
    await _requirement(session, requirement_id)
    versions = (
        await session.execute(
            select(RequirementVersion)
            .options(selectinload(RequirementVersion.features))
            .where(RequirementVersion.requirement_id == requirement_id)
            .order_by(RequirementVersion.version_number.desc())
        )
    ).scalars()
    return RequirementVersionListResponse(
        items=[RequirementVersionResponse.model_validate(item) for item in versions]
    )


@router.get(
    "/{requirement_id}/versions/{version_number}",
    response_model=RequirementVersionResponse,
)
async def get_requirement_version(
    requirement_id: int,
    version_number: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RequirementVersionResponse:
    version = await session.scalar(
        select(RequirementVersion)
        .options(selectinload(RequirementVersion.features))
        .where(
            RequirementVersion.requirement_id == requirement_id,
            RequirementVersion.version_number == version_number,
        )
    )
    if version is None:
        raise RequirementNotFoundError(
            f"requirement {requirement_id} version {version_number} was not found"
        )
    return RequirementVersionResponse.model_validate(version)


@router.get("/{requirement_id}/diff", response_model=RequirementDiffResponse)
async def get_requirement_diff(
    requirement_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    to_version: Annotated[int | None, Query(gt=0)] = None,
) -> RequirementDiffResponse:
    requirement = await _requirement(session, requirement_id)
    target_number = to_version
    if target_number is None:
        current = await session.get(RequirementVersion, requirement.current_version_id)
        if current is None:
            raise RequirementNotFoundError("requirement has no current version")
        target = current
    else:
        selected_target = await session.scalar(
            select(RequirementVersion).where(
                RequirementVersion.requirement_id == requirement_id,
                RequirementVersion.version_number == target_number,
            )
        )
        if selected_target is None:
            raise RequirementNotFoundError(
                f"requirement {requirement_id} version {target_number} was not found"
            )
        target = selected_target
    return RequirementDiffResponse(
        requirement_id=requirement_id,
        from_version=target.version_number - 1 if target.version_number > 1 else None,
        to_version=target.version_number,
        diff=target.diff_snapshot,
    )


async def _requirement(session: AsyncSession, requirement_id: int) -> Requirement:
    requirement = await session.get(Requirement, requirement_id)
    if requirement is None:
        raise RequirementNotFoundError(f"requirement {requirement_id} was not found")
    return requirement


async def _current_features(
    session: AsyncSession,
    requirement: Requirement,
) -> list[RequirementFeature]:
    if requirement.current_version_id is None:
        return []
    return list(
        (
            await session.execute(
                select(RequirementFeature)
                .where(RequirementFeature.version_id == requirement.current_version_id)
                .order_by(RequirementFeature.sort_order)
            )
        ).scalars()
    )


async def _lineage_by_feature(
    session: AsyncSession,
    requirement_id: int,
    feature_keys: list[str],
) -> dict[str, list[FeatureLineage]]:
    if not feature_keys:
        return {}
    rows = (
        await session.execute(
            select(FeatureLineage)
            .join(
                RequirementVersion,
                RequirementVersion.id == FeatureLineage.introduced_version_id,
            )
            .where(FeatureLineage.feature_key.in_(feature_keys))
            .where(RequirementVersion.requirement_id == requirement_id)
            .order_by(FeatureLineage.created_at, FeatureLineage.id)
        )
    ).scalars()
    result: dict[str, list[FeatureLineage]] = {}
    for row in rows:
        result.setdefault(row.feature_key, []).append(row)
    return result


def _feature_response(
    feature: RequirementFeature,
    lineage: list[FeatureLineage],
) -> RequirementFeatureResponse:
    response = RequirementFeatureResponse.model_validate(feature)
    response.lineage = [
        FeatureLineageResponse.model_validate(item) for item in lineage
    ]
    return response
