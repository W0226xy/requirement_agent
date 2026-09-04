from fastapi import FastAPI

from requirement_agent.api.analysis import router as analysis_router
from requirement_agent.api.errors import application_error_handler
from requirement_agent.api.health import router as health_router
from requirement_agent.api.requirements import router as requirements_router
from requirement_agent.api.reviews import router as reviews_router
from requirement_agent.api.sources import router as sources_router
from requirement_agent.shared.config import Settings, get_settings
from requirement_agent.shared.errors import ApplicationError


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    application = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    application.add_exception_handler(ApplicationError, application_error_handler)
    application.include_router(health_router)
    application.include_router(sources_router)
    application.include_router(analysis_router)
    application.include_router(reviews_router)
    application.include_router(requirements_router)
    return application


app = create_app()
