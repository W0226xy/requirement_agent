"""Repeatable, offline Recall@K evaluation for :class:`HybridRetriever`.

This module intentionally creates an in-memory database containing only fixed
approved requirement projections.  It never creates SourceRecord, ReviewTask,
or RequirementVersion records in an application's configured database.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from requirement_agent.ai.retrieval.hybrid import HybridRetriever, RetrievalWeights
from requirement_agent.ai.schemas.retrieval import RequirementCandidate
from requirement_agent.infrastructure.database.base import Base
from requirement_agent.infrastructure.database.models import (
    Requirement,
    RequirementEmbedding,
    RequirementVersion,
)
from requirement_agent.shared.enums import RequirementChangeType, RequirementStatus

DEFAULT_KS = (1, 3, 5, 10)
EVALUATION_CONFIGS = {
    "keyword_only": RetrievalWeights(keyword=1.0, vector=0.0, business=0.0),
    "vector_only": RetrievalWeights(keyword=0.0, vector=1.0, business=0.0),
    "hybrid": RetrievalWeights(keyword=0.4, vector=0.4, business=0.2),
}


@dataclass(frozen=True)
class EvaluationQuery:
    requirement_summary: str
    requirement_description: str
    functional_modules: list[str]

    @property
    def text(self) -> str:
        return f"{self.requirement_summary}\n{self.requirement_description}"


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    query: EvaluationQuery
    relevant_requirement_keys: list[str]
    relation_type: str
    note: str
    skip_reason: str | None = None


class Retriever(Protocol):
    async def search(
        self, query: str, *, source_record_id: int | None, query_modules: list[str]
    ) -> list[RequirementCandidate]: ...


RetrieverFactory = Callable[[RetrievalWeights], Retriever]


def load_dataset(path: Path) -> list[EvaluationCase]:
    """Load and validate the hand-labelled JSONL corpus without invoking an LLM."""
    cases: list[EvaluationCase] = []
    seen_case_ids: set[str] = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            query = payload["query"]
            case = EvaluationCase(
                case_id=_required_text(payload, "case_id"),
                query=EvaluationQuery(
                    requirement_summary=_required_text(query, "requirement_summary"),
                    requirement_description=_required_text(query, "requirement_description"),
                    functional_modules=_string_list(query, "functional_modules", non_empty=True),
                ),
                relevant_requirement_keys=_string_list(
                    payload, "relevant_requirement_keys", non_empty=False
                ),
                relation_type=_required_text(payload, "relation_type"),
                note=_required_text(payload, "note"),
                skip_reason=_optional_text(payload, "skip_reason"),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"invalid RAG evaluation case at {path}:{line_number}: {exc}"
            ) from exc
        if case.case_id in seen_case_ids:
            raise ValueError(f"duplicate case_id at {path}:{line_number}: {case.case_id}")
        if not case.relevant_requirement_keys and not case.skip_reason:
            raise ValueError(
                f"empty relevant_requirement_keys needs skip_reason at {path}:{line_number}"
            )
        seen_case_ids.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError(f"RAG evaluation dataset is empty: {path}")
    return cases


def recall_at_k(
    retrieved_requirement_keys: Iterable[str],
    relevant_requirement_keys: Iterable[str],
    k: int,
) -> float | None:
    """Recall of unique relevant requirement keys in the first *k* unique results.

    ``None`` denotes an unscored case (an empty gold set), so callers cannot
    accidentally include it in a macro average.
    """
    if k <= 0:
        return 0.0
    relevant = set(relevant_requirement_keys)
    if not relevant:
        return None
    retrieved = _unique_keys(retrieved_requirement_keys)[:k]
    return len(set(retrieved).intersection(relevant)) / len(relevant)


async def run_evaluation(
    cases: list[EvaluationCase], ks: Iterable[int], retriever_factory: RetrieverFactory
) -> dict[str, Any]:
    normalized_ks = _validate_ks(ks)
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_sample_count": len(cases),
        "valid_sample_count": sum(bool(case.relevant_requirement_keys) for case in cases),
        "ks": normalized_ks,
        "schemes": {},
    }
    for scheme_name, weights in EVALUATION_CONFIGS.items():
        retriever = retriever_factory(weights)
        details: list[dict[str, Any]] = []
        totals = {k: [] for k in normalized_ks}
        for case in cases:
            candidates = await retriever.search(
                case.query.text,
                source_record_id=None,
                query_modules=case.query.functional_modules,
            )
            retrieved = _unique_keys(candidate.requirement_key for candidate in candidates)
            per_k = {
                str(k): recall_at_k(retrieved, case.relevant_requirement_keys, k)
                for k in normalized_ks
            }
            for k in normalized_ks:
                if per_k[str(k)] is not None:
                    totals[k].append(per_k[str(k)])
            details.append(
                {
                    "case_id": case.case_id,
                    "relation_type": case.relation_type,
                    "relevant_requirement_keys": case.relevant_requirement_keys,
                    "skip_reason": case.skip_reason,
                    "top_requirement_keys": {str(k): retrieved[:k] for k in normalized_ks},
                    "hits": {
                        str(k): sorted(
                            set(retrieved[:k]).intersection(case.relevant_requirement_keys)
                        )
                        for k in normalized_ks
                    },
                    "recall_at_k": per_k,
                }
            )
        report["schemes"][scheme_name] = {
            "weights": asdict(weights),
            "macro_recall_at_k": {
                str(k): (sum(totals[k]) / len(totals[k]) if totals[k] else None)
                for k in normalized_ks
            },
            "cases": details,
        }
    return report


class DeterministicEmbedding:
    """A local, stable character n-gram embedding for the offline corpus."""

    model_name = "rag-eval-deterministic-v1"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_embedding(text) for text in texts]


async def seed_evaluation_requirements(session: AsyncSession) -> None:
    """Insert stable, already-approved current requirement projections only."""
    for key, title, modules, content in _SEED_REQUIREMENTS:
        requirement = Requirement(
            requirement_key=key,
            title=title,
            status=RequirementStatus.ACTIVE,
            functional_modules=modules,
            extra_fields={},
        )
        session.add(requirement)
        await session.flush()
        version = RequirementVersion(
            requirement_id=requirement.id,
            version_number=1,
            parent_version_id=None,
            change_type=RequirementChangeType.INITIAL,
            version_title=title,
            requirement_snapshot={},
            diff_snapshot={},
            change_reason="offline RAG evaluation seed",
            created_by="rag-eval",
            reviewed_by="rag-eval",
        )
        session.add(version)
        await session.flush()
        requirement.current_version_id = version.id
        session.add(
            RequirementEmbedding(
                requirement_id=requirement.id,
                version_id=version.id,
                requirement_key=key,
                version_number=1,
                feature_key=None,
                title=title,
                module=modules[0],
                status=RequirementStatus.ACTIVE.value,
                functional_modules=modules,
                features=[],
                sources=[],
                content=content,
                embedding=_embedding(f"{title}\n{content}"),
                embedding_model=DeterministicEmbedding.model_name,
            )
        )
    await session.commit()


async def evaluate_offline(dataset: Path, ks: Iterable[int]) -> dict[str, Any]:
    """Evaluate against a fresh in-memory database; no configured DB is opened."""
    cases = load_dataset(dataset)
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await seed_evaluation_requirements(session)
            embedding = DeterministicEmbedding()
            return await run_evaluation(
                cases,
                ks,
                lambda weights: HybridRetriever(
                    session, embedding, weights, candidate_limit=max(_validate_ks(ks)),
                    min_similarity_score=0.0,
                ),
            )
    finally:
        await engine.dispose()


def _unique_keys(keys: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def _validate_ks(ks: Iterable[int]) -> list[int]:
    result = sorted(set(ks))
    if not result or any(k <= 0 for k in result):
        raise ValueError("ks must contain positive integers")
    return result


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{key} must be a non-empty string when supplied")
    return value


def _string_list(payload: dict[str, Any], key: str, *, non_empty: bool) -> list[str]:
    value = payload[key]
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{key} must be a list of non-empty strings")
    if non_empty and not value:
        raise ValueError(f"{key} must not be empty")
    if len(set(value)) != len(value):
        raise ValueError(f"{key} must not contain duplicate keys")
    return value


def _embedding(text: str, dimensions: int = 64) -> list[float]:
    compact = re.sub(r"\s+", "", text.lower())
    vector = [0.0] * dimensions
    for token in (
        compact[index : index + 2] for index in range(max(0, len(compact) - 1))
    ):
        slot = (
            int.from_bytes(hashlib.blake2b(token.encode(), digest_size=2).digest(), "big")
            % dimensions
        )
        vector[slot] += 1.0
    return vector


_SEED_REQUIREMENTS: list[tuple[str, str, list[str], str]] = [
    (
        "REQ-EVAL-001", "车辆保养提醒", ["车辆保养提醒", "车辆中心"],
        "首页和车辆中心展示下次保养日期、剩余保养里程与保养提醒入口。",
    ),
    ("REQ-EVAL-002", "车辆故障告警", ["车辆告警", "车辆中心"], "车辆中心展示故障码、告警级别和处理建议。"),
    ("REQ-EVAL-003", "充电站地图", ["充电服务", "地图"], "地图展示附近充电桩、空闲状态、价格和导航入口。"),
    ("REQ-EVAL-004", "充电预约", ["充电服务", "预约"], "用户选择充电站、时段并提交充电预约，支持取消预约。"),
    ("REQ-EVAL-005", "行程历史", ["行程", "车辆中心"], "按日期查询历史行程、里程、耗时和路线。"),
    ("REQ-EVAL-006", "电子围栏通知", ["车辆安全", "消息通知"], "车辆驶入或驶出电子围栏时向车主发送推送通知。"),
    ("REQ-EVAL-007", "远程空调控制", ["远程控制", "空调"], "用户可远程开启关闭空调，设置温度并查看执行结果。"),
    ("REQ-EVAL-008", "车辆门锁控制", ["远程控制", "车辆安全"], "用户可远程锁车、解锁，并收到门锁状态通知。"),
    ("REQ-EVAL-009", "驾驶行为评分", ["驾驶行为", "车辆中心"], "根据急加速、急刹车和超速计算驾驶评分并给出建议。"),
    ("REQ-EVAL-010", "保险到期提醒", ["保险服务", "消息通知"], "保险到期前展示剩余天数并发送续保提醒。"),
    ("REQ-EVAL-011", "车辆定位", ["车辆定位", "地图"], "地图实时显示车辆位置，支持刷新位置和查看停车地址。"),
    ("REQ-EVAL-012", "道路救援", ["道路救援", "服务"], "用户提交道路救援地点、故障描述并查看救援进度。"),
]


def _print_summary(report: dict[str, Any]) -> None:
    ks = report["ks"]
    print(
        f"RAG evaluation: {report['valid_sample_count']}/"
        f"{report['dataset_sample_count']} scored cases"
    )
    print("scheme         " + "  ".join(f"Recall@{k}" for k in ks))
    for name, scheme in report["schemes"].items():
        scores = scheme["macro_recall_at_k"]
        print(f"{name:14}" + "  ".join(f"{scores[str(k)]:.3f}" for k in ks))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the isolated offline RAG Recall@K evaluation"
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--ks", type=int, nargs="+", default=list(DEFAULT_KS))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = asyncio.run(evaluate_offline(args.dataset, args.ks))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _print_summary(report)
    print(f"report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
