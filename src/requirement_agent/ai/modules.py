from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from requirement_agent.infrastructure.database.models import (
    Requirement,
    RequirementFeature,
)

MODULE_SUFFIXES = ("功能模块", "模块", "功能")


async def load_existing_modules(session: AsyncSession) -> list[str]:
    requirement_modules = (
        await session.execute(select(Requirement.functional_modules))
    ).scalars()
    feature_modules = (
        await session.execute(select(RequirementFeature.module).distinct())
    ).scalars()
    modules = {
        module.strip()
        for values in requirement_modules
        for module in values
        if module.strip()
    }
    modules.update(module.strip() for module in feature_modules if module.strip())
    return sorted(modules)


def normalize_modules(
    source_content: str,
    proposed_modules: list[str],
    existing_modules: list[str],
) -> list[str]:
    mentioned = [
        module
        for module in existing_modules
        if module.casefold() in source_content.casefold()
    ]
    if mentioned:
        return _deduplicate(mentioned)

    canonical_existing = {
        _canonical_module(module): module for module in existing_modules
    }
    matched: list[str] = []
    novel: list[str] = []
    for proposed in proposed_modules:
        cleaned = proposed.strip()
        if not cleaned:
            continue
        existing = canonical_existing.get(_canonical_module(cleaned))
        if existing is not None:
            matched.append(existing)
        else:
            novel.append(cleaned)
    return _deduplicate(matched or novel)


def normalize_operation_module(
    source_content: str,
    proposed_module: str,
    existing_modules: list[str],
) -> str:
    modules = normalize_modules(source_content, [proposed_module], existing_modules)
    return modules[0] if modules else proposed_module


def _canonical_module(value: str) -> str:
    canonical = "".join(value.casefold().split())
    for suffix in MODULE_SUFFIXES:
        if canonical.endswith(suffix) and len(canonical) > len(suffix):
            return canonical[: -len(suffix)]
    return canonical


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
