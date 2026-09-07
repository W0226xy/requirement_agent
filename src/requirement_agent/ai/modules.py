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
    source_content: str,#用户原始需求文本
    proposed_modules: list[str],#大模型建议的模块列表
    existing_modules: list[str],#数据库已有模块列表
) -> list[str]:
    #1.优先找原始文本中直接提到的已有模块
    mentioned = [
        module
        for module in existing_modules
        if module.casefold() in source_content.casefold()#忽略大小写匹配
    ]
    if mentioned:
        return _deduplicate(mentioned)
    #2.如果没有直接提到的已有模块，则匹配大模型建议的模块
    canonical_existing = {
        #_canonical_module()会将模块名称转换成便于比较的“规范形式”。
        _canonical_module(module): module for module in existing_modules
    }
    matched: list[str] = []#用于保存能够匹配已有模块的结果。
    novel: list[str] = []#用于保存数据库中没有出现过的新模块。
    for proposed in proposed_modules:#遍历模型建议的模块
        cleaned = proposed.strip()#去掉模块名称的前后空白字符
        if not cleaned:
            continue
        existing = canonical_existing.get(_canonical_module(cleaned))#尝试在已有模块中找到与当前建议模块匹配的模块
        if existing is not None:
            matched.append(existing)
        else:#如果没有匹配到已有模块，则将其视为新模块，添加到 novel 列表中
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
    canonical = "".join(value.casefold().split())#先统一大小写并去掉所有空白字符
    for suffix in MODULE_SUFFIXES:#遍历常见模块后缀（功能模块、模块、功能）
        if canonical.endswith(suffix) and len(canonical) > len(suffix):#如果模块名称以某个后缀结尾且长度大于后缀长度，则去掉该后缀
            return canonical[: -len(suffix)]
    return canonical


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))#去重并保持原有顺序
