from fastapi.testclient import TestClient

from apps.api.main import app
from requirement_agent.infrastructure.health import HealthChecker, get_health_checker


class ReadyHealthChecker:
    async def check(self) -> dict[str, str]:
        return {"postgres": "ok", "redis": "ok", "minio": "ok"}


class FailedHealthChecker:
    async def check(self) -> dict[str, str]:
        return {"postgres": "ok", "redis": "error:ConnectionError", "minio": "ok"}


def test_liveness() -> None:
    response = TestClient(app).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


def test_readiness() -> None:
    app.dependency_overrides[get_health_checker] = ReadyHealthChecker
    try:
        response = TestClient(app).get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readiness_reports_dependency_failure() -> None:
    app.dependency_overrides[get_health_checker] = FailedHealthChecker
    try:
        response = TestClient(app).get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def assert_health_checker_contract(checker: HealthChecker) -> None:
    assert checker is not None

