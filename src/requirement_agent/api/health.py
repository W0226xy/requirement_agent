from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from requirement_agent.infrastructure.health import HealthChecker, get_health_checker
from requirement_agent.shared.config import get_settings

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    status: str
    version: str


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, str]


@router.get("/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    settings = get_settings()
    return LivenessResponse(status="ok", version=settings.app_version)


@router.get("/ready", response_model=ReadinessResponse)
async def readiness(
    response: Response,
    checker: Annotated[HealthChecker, Depends(get_health_checker)],
) -> ReadinessResponse:
    checks = await checker.check()
    is_ready = all(result == "ok" for result in checks.values())
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ready" if is_ready else "not_ready", checks=checks)

