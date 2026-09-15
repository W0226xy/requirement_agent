from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from requirement_agent.ai.schemas.analysis import ProposedOperation
from requirement_agent.infrastructure.database.models import (
    AuditLog,
    FeatureLineage,
    Requirement,
    RequirementFeature,
    RequirementVersion,
    ReviewTask,
    SourceRecord,
)
from requirement_agent.shared.enums import (
    AuditActionType,
    AuditEntityType,
    ChangeOperation,
    FeatureStatus,
    LineageOperationType,
    ProcessingStatus,
    RequirementChangeType,
    RequirementStatus,
    ReviewDecision,
    ReviewStatus,
)
from requirement_agent.shared.errors import (
    RequirementNotFoundError,
    ReviewStateError,
    ReviewTaskNotFoundError,
    VersionOperationError,
)


@dataclass
class MutableFeature:
    feature_key: str
    module: str
    feature_title: str
    feature_description: str
    acceptance_criteria: list[str]
    feature_status: FeatureStatus
    sort_order: int

    @classmethod
    def from_model(cls, feature: RequirementFeature) -> "MutableFeature":
        return cls(
            feature_key=feature.feature_key,
            module=feature.module,
            feature_title=feature.feature_title,
            feature_description=feature.feature_description,
            acceptance_criteria=list(feature.acceptance_criteria),
            feature_status=feature.feature_status,
            sort_order=feature.sort_order,
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "feature_key": self.feature_key,
            "module": self.module,
            "feature_title": self.feature_title,
            "feature_description": self.feature_description,
            "acceptance_criteria": self.acceptance_criteria,
            "feature_status": self.feature_status.value,
            "sort_order": self.sort_order,
        }


class VersionCommitService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def approve(
        self,
        *,
        review_task_id: int,
        reviewer_id: str,
        decision: ReviewDecision,
        title: str | None,
        target_requirement_key: str | None,
        expected_requirement_id: int | None,
        expected_current_version: int | None,
        operations: list[ProposedOperation],
        comment: str | None,
    ) -> RequirementVersion:
        try:
            async with self._session.begin():
                task = await self._load_review(review_task_id)
                if task.committed_version_id is not None:
                    version = await self._session.get(
                        RequirementVersion, task.committed_version_id
                    )
                    if version is None:
                        raise ReviewStateError("committed review has no version")
                    return version
                if task.review_status != ReviewStatus.PENDING:
                    raise ReviewStateError(
                        f"review task is already {task.review_status.value}"
                    )
                if any(op.source_record_id != task.source_record_id for op in operations):
                    raise VersionOperationError(
                        "all operations must reference the review source record"
                    )

                requirement, current_version = await self._resolve_requirement(
                    decision,
                    title,
                    target_requirement_key,
                    expected_requirement_id,
                    expected_current_version,
                )
                features = (
                    [MutableFeature.from_model(item) for item in current_version.features]
                    if current_version is not None
                    else []
                )
                changes = self._apply_operations(features, operations)
                version_number = (
                    current_version.version_number + 1 if current_version is not None else 1
                )
                version_title = title or requirement.title
                modules = sorted(
                    {
                        feature.module
                        for feature in features
                        if feature.feature_status == FeatureStatus.ACTIVE
                    }
                )
                snapshot: dict[str, object] = {
                    "requirement_key": requirement.requirement_key,
                    "title": version_title,
                    "status": requirement.status.value,
                    "functional_modules": modules,
                    "features": [item.snapshot() for item in features],
                }
                version = RequirementVersion(
                    requirement_id=requirement.id,
                    version_number=version_number,
                    parent_version_id=current_version.id if current_version else None,
                    change_type=(
                        RequirementChangeType.UPDATE
                        if current_version
                        else RequirementChangeType.INITIAL
                    ),
                    version_title=version_title,
                    requirement_snapshot=snapshot,
                    diff_snapshot={"operations": changes},
                    change_reason=comment or "; ".join(op.reason for op in operations),
                    created_by=reviewer_id,
                    reviewed_by=reviewer_id,
                )
                self._session.add(version)
                await self._session.flush()
                for feature in features:
                    self._session.add(
                        RequirementFeature(
                            feature_key=feature.feature_key,
                            version_id=version.id,
                            module=feature.module,
                            feature_title=feature.feature_title,
                            feature_description=feature.feature_description,
                            acceptance_criteria=feature.acceptance_criteria,
                            feature_status=feature.feature_status,
                            sort_order=feature.sort_order,
                        )
                    )
                lineage_map = {
                    ChangeOperation.ADD: LineageOperationType.INTRODUCED,
                    ChangeOperation.MODIFY: LineageOperationType.MODIFIED,
                    ChangeOperation.DELETE: LineageOperationType.DELETED,
                    ChangeOperation.RESTORE: LineageOperationType.RESTORED,
                }
                for operation, change in zip(operations, changes, strict=True):
                    self._session.add(
                        FeatureLineage(
                            feature_key=str(change["feature_key"]),
                            source_record_id=task.source_record_id,
                            introduced_version_id=version.id,
                            operation_type=lineage_map[operation.operation],
                            evidence_text=operation.reason,
                        )
                    )

                requirement.title = version_title
                requirement.functional_modules = modules
                requirement.current_version_id = version.id
                task.target_requirement_id = requirement.id
                task.review_status = ReviewStatus.APPROVED
                task.decision = decision
                task.approved_operations = [
                    operation.model_dump(mode="json") for operation in operations
                ]
                task.reviewer_id = reviewer_id
                task.review_comment = comment
                task.reviewed_at = datetime.now(UTC)
                task.committed_version_id = version.id
                source = await self._session.get(SourceRecord, task.source_record_id)
                if source is None:
                    raise ReviewStateError("review source no longer exists")
                source.processing_status = ProcessingStatus.VERSIONED
                self._session.add(
                    AuditLog(
                        actor_id=reviewer_id,
                        action_type=AuditActionType.REQUIREMENT_VERSION_CREATED,
                        entity_type=AuditEntityType.REQUIREMENT_VERSION,
                        entity_id=str(version.id),
                        before_data=(
                            current_version.requirement_snapshot
                            if current_version is not None
                            else None
                        ),
                        after_data=snapshot,
                    )
                )
                self._session.add(
                    AuditLog(
                        actor_id=reviewer_id,
                        action_type=AuditActionType.REVIEW_APPROVED,
                        entity_type=AuditEntityType.REVIEW_TASK,
                        entity_id=str(task.id),
                        after_data={
                            "requirement_id": requirement.id,
                            "version_id": version.id,
                        },
                    )
                )
            return version
        except Exception:
            await self._session.rollback()
            raise

    async def _load_review(self, review_task_id: int) -> ReviewTask:
        task = await self._session.scalar(
            select(ReviewTask)
            .where(ReviewTask.id == review_task_id)
            .with_for_update()
        )
        if task is None:
            raise ReviewTaskNotFoundError(
                f"review task {review_task_id} was not found"
            )
        return task

    async def _resolve_requirement(
        self,
        decision: ReviewDecision,
        title: str | None,
        target_requirement_key: str | None,
        expected_requirement_id: int | None,
        expected_current_version: int | None,
    ) -> tuple[Requirement, RequirementVersion | None]:
        if decision == ReviewDecision.CREATE:
            if (
                target_requirement_key is not None
                or expected_requirement_id is not None
                or expected_current_version is not None
                or not title
            ):
                raise VersionOperationError(
                    "create requires title and must not specify merge target fields"
                )
            requirement = Requirement(
                requirement_key=f"REQ-{uuid4().hex[:8].upper()}",
                title=title,
                status=RequirementStatus.ACTIVE,
                functional_modules=[],
                extra_fields={},
            )
            self._session.add(requirement)
            await self._session.flush()
            return requirement, None
        if (
            target_requirement_key is None
            or expected_requirement_id is None
            or expected_current_version is None
        ):
            raise VersionOperationError(
                "merge requires target_requirement_key, expected_requirement_id, "
                "and expected_current_version"
            )
        existing_requirement = await self._session.scalar(
            select(Requirement)
            .where(Requirement.requirement_key == target_requirement_key)
            .with_for_update()
        )
        if existing_requirement is None:
            raise RequirementNotFoundError(
                f"requirement {target_requirement_key} was not found"
            )
        if existing_requirement.id != expected_requirement_id:
            raise VersionOperationError(
                "selected requirement does not match expected_requirement_id"
            )
        if existing_requirement.current_version_id is None:
            raise VersionOperationError("target requirement has no current version")
        current = await self._session.scalar(
            select(RequirementVersion)
            .options(selectinload(RequirementVersion.features))
            .where(RequirementVersion.id == existing_requirement.current_version_id)
        )
        if current is None:
            raise VersionOperationError("current requirement version was not found")
        if current.version_number != expected_current_version:
            raise ReviewStateError(
                "target requirement changed; refresh and choose its current version"
            )
        return existing_requirement, current

    @staticmethod
    def _apply_operations(
        features: list[MutableFeature],
        operations: list[ProposedOperation],
    ) -> list[dict[str, object]]:
        by_key = {feature.feature_key: feature for feature in features}
        next_number = max(
            (
                int(feature.feature_key.removeprefix("FEAT-"))
                for feature in features
                if feature.feature_key.removeprefix("FEAT-").isdigit()
            ),
            default=0,
        )
        changes: list[dict[str, object]] = []
        for operation in operations:
            before: dict[str, object] | None = None
            if operation.operation == ChangeOperation.ADD:
                next_number += 1
                key = f"FEAT-{next_number:03d}"
                content = operation.content
                if content is None:
                    raise VersionOperationError("add content is required")
                feature = MutableFeature(
                    feature_key=key,
                    module=content.module,
                    feature_title=content.feature_title,
                    feature_description=content.feature_description,
                    acceptance_criteria=list(content.acceptance_criteria),
                    feature_status=FeatureStatus.ACTIVE,
                    sort_order=len(features) + 1,
                )
                features.append(feature)
                by_key[key] = feature
            else:
                key = operation.feature_key or ""
                existing_feature = by_key.get(key)
                if existing_feature is None:
                    raise VersionOperationError(f"feature {key} does not exist")
                feature = existing_feature
                before = feature.snapshot()
                if operation.operation == ChangeOperation.DELETE:
                    if feature.feature_status == FeatureStatus.DELETED:
                        raise VersionOperationError(f"feature {key} is already deleted")
                    feature.feature_status = FeatureStatus.DELETED
                elif operation.operation == ChangeOperation.RESTORE:
                    if feature.feature_status != FeatureStatus.DELETED:
                        raise VersionOperationError(f"feature {key} is not deleted")
                    VersionCommitService._replace_content(feature, operation)
                    feature.feature_status = FeatureStatus.ACTIVE
                else:
                    if feature.feature_status == FeatureStatus.DELETED:
                        raise VersionOperationError(f"deleted feature {key} cannot be modified")
                    VersionCommitService._replace_content(feature, operation)
            changes.append(
                {
                    "operation": operation.operation.value,
                    "feature_key": key,
                    "before": before,
                    "after": feature.snapshot(),
                    "reason": operation.reason,
                }
            )
        return changes

    @staticmethod
    def _replace_content(
        feature: MutableFeature,
        operation: ProposedOperation,
    ) -> None:
        content = operation.content
        if content is None:
            raise VersionOperationError(f"{operation.operation.value} content is required")
        feature.module = content.module
        feature.feature_title = content.feature_title
        feature.feature_description = content.feature_description
        feature.acceptance_criteria = list(content.acceptance_criteria)
