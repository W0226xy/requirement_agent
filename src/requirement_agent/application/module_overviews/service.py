import hashlib
import json
import logging
from dataclasses import dataclass

from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from requirement_agent.ai.llm.base import ChatModel
from requirement_agent.ai.schemas.common import StrictAIModel
from requirement_agent.infrastructure.database.models import (
    ModuleOverview,
    ModuleOverviewRevision,
    Requirement,
    RequirementVersion,
)
from requirement_agent.shared.enums import FeatureStatus, RequirementStatus

logger = logging.getLogger(__name__)


class ModuleOverviewOutput(StrictAIModel):
    overview: str = Field(max_length=500)
    core_capabilities: list[str] = Field(max_length=8)
    pending_items: list[str] = Field(max_length=5)
    referenced_requirement_keys: list[str]


@dataclass(frozen=True)
class Trigger:
    requirement_key: str | None = None
    version_number: int | None = None
    change_type: str | None = None
    change_reason: str | None = None


class ModuleOverviewService:
    """Builds a module snapshot from current active formal requirements only."""

    def __init__(self, session: AsyncSession, llm: ChatModel) -> None:
        self._session = session
        self._llm = llm

    async def refresh(self, module_name: str, trigger: Trigger | None = None) -> None:
        # A PostgreSQL advisory lock is process-safe and scoped to a module.  A task
        # that loses the lock can safely exit: the lock holder always re-reads state.
        trigger = trigger or Trigger()
        if not await self._acquire_lock(module_name):
            return
        try:
            requirements = await self._active_requirements(module_name)
            snapshot = _snapshot(requirements)
            fingerprint = _fingerprint(snapshot)
            row = await self._overview(module_name, for_update=True)
            if row is not None and row.status == "ready" and row.source_fingerprint == fingerprint:
                return
            if row is None:
                row = ModuleOverview(module_name=module_name, overview=None, core_capabilities=[], pending_items=[], requirement_count=0, source_snapshot=[], status="updating")
                self._session.add(row)
                await self._session.flush()
            if not requirements:
                row.overview = None
                row.core_capabilities = []
                row.pending_items = []
                row.requirement_count = 0
                row.source_snapshot = []
                row.source_fingerprint = fingerprint
                row.status = "empty"
                row.last_error = None
                await self._session.commit()
                return

            old_overview = row.overview or ""
            row.status = "updating"
            row.last_error = None
            await self._session.commit()
            try:
                output = await self._generate(module_name, old_overview, requirements, trigger)
                valid_keys = {requirement.requirement_key for requirement, _ in requirements}
                if not set(output.referenced_requirement_keys).issubset(valid_keys):
                    raise ValueError("LLM referenced a requirement outside the current module snapshot")
                # The input may have changed while the LLM was running. Never let this
                # older task replace a newer formal-requirement snapshot.
                current = await self._active_requirements(module_name)
                current_snapshot = _snapshot(current)
                if _fingerprint(current_snapshot) != fingerprint:
                    return
                row = await self._overview(module_name, for_update=True)
                if row is None:
                    return
                if row.status == "ready" and row.source_fingerprint == fingerprint:
                    return
                row.overview = output.overview
                row.core_capabilities = output.core_capabilities
                row.pending_items = output.pending_items
                row.requirement_count = len(requirements)
                row.source_snapshot = snapshot
                row.source_fingerprint = fingerprint
                row.status = "ready"
                row.last_error = None
                self._session.add(ModuleOverviewRevision(
                    module_overview_id=row.id, overview=output.overview,
                    core_capabilities=output.core_capabilities, pending_items=output.pending_items,
                    source_snapshot=snapshot, source_fingerprint=fingerprint,
                    trigger_requirement_key=trigger.requirement_key,
                    trigger_version_number=trigger.version_number,
                ))
                await self._session.commit()
            except Exception as exc:
                await self._session.rollback()
                row = await self._overview(module_name, for_update=True)
                if row is not None:
                    # Do not erase a known-good overview on an LLM outage.
                    row.status = "failed"
                    row.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                    await self._session.commit()
                logger.exception("module_overview_refresh_failed module=%s", module_name)
        finally:
            await self._release_lock(module_name)

    async def _active_requirements(self, module_name: str) -> list[tuple[Requirement, RequirementVersion]]:
        rows = (await self._session.execute(
            select(Requirement, RequirementVersion)
            .join(RequirementVersion, Requirement.current_version_id == RequirementVersion.id)
            .options(selectinload(RequirementVersion.features))
            .where(Requirement.status == RequirementStatus.ACTIVE)
            .order_by(Requirement.requirement_key)
        )).all()
        return [(requirement, version) for requirement, version in rows if module_name in _modules(requirement, version)]

    async def _overview(self, module_name: str, *, for_update: bool = False) -> ModuleOverview | None:
        query = select(ModuleOverview).where(ModuleOverview.module_name == module_name)
        if for_update:
            query = query.with_for_update()
        return await self._session.scalar(query)

    async def _generate(self, module_name: str, old_overview: str, requirements: list[tuple[Requirement, RequirementVersion]], trigger: Trigger) -> ModuleOverviewOutput:
        payload = {
            "module_name": module_name, "old_overview": old_overview,
            "trigger": {"requirement_key": trigger.requirement_key, "version_number": trigger.version_number, "change_type": trigger.change_type, "change_reason": trigger.change_reason},
            "requirements": [_requirement_input(requirement, version) for requirement, version in requirements],
        }
        prompt = """你是产品需求模块概述助手。只能基于提供的正式需求事实生成概述，不得虚构需求、功能、状态、风险、时间或 REQ 编号。overview 用中文简洁描述模块业务目标、覆盖范围和当前能力；core_capabilities 只能列明确支持的能力；pending_items 只能列明确存在但尚未确认、冲突或风险事项，没有则空数组；referenced_requirement_keys 只能引用输入 requirement_key 且覆盖实际使用需求。overview 最多500字，核心能力最多8项，待确认最多5项。输出必须仅为符合 JSON Schema 的 JSON：{overview:string,core_capabilities:string[],pending_items:string[],referenced_requirement_keys:string[]}。"""
        raw = await self._llm.complete([{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], analysis_type="module_overview")
        return ModuleOverviewOutput.model_validate_json(raw)

    async def _acquire_lock(self, module_name: str) -> bool:
        if self._session.bind is None or self._session.bind.dialect.name != "postgresql":
            return True
        return bool(await self._session.scalar(select(func.pg_try_advisory_lock(_lock_id(module_name)))))

    async def _release_lock(self, module_name: str) -> None:
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            await self._session.scalar(select(func.pg_advisory_unlock(_lock_id(module_name))))


def _modules(requirement: Requirement, version: RequirementVersion) -> set[str]:
    return {item for item in requirement.functional_modules} | {feature.module for feature in version.features if feature.feature_status == FeatureStatus.ACTIVE}


def _snapshot(requirements: list[tuple[Requirement, RequirementVersion]]) -> list[dict[str, object]]:
    return [{"requirement_key": requirement.requirement_key, "version_number": version.version_number} for requirement, version in requirements]


def _fingerprint(snapshot: list[dict[str, object]]) -> str:
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _requirement_input(requirement: Requirement, version: RequirementVersion) -> dict[str, object]:
    features = [feature for feature in version.features if feature.feature_status == FeatureStatus.ACTIVE]
    return {"requirement_key": requirement.requirement_key, "version_number": version.version_number, "title": requirement.title, "description": _short("；".join(feature.feature_description for feature in features), 1600), "features": [{"title": feature.feature_title, "description": _short(feature.feature_description, 700), "acceptance_criteria": feature.acceptance_criteria} for feature in features]}


def _short(value: str, limit: int) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _lock_id(module_name: str) -> int:
    return int.from_bytes(hashlib.sha256(module_name.encode()).digest()[:8], "big", signed=True)
