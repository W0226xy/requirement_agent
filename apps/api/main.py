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
    #settings：可选的配置对象。
    #可以传入一个 Settings 实例来覆盖默认配置。如果没有传入，则会使用 get_settings() 函数获取默认配置。


    app_settings = settings or get_settings()

    #创建一个 FastAPI 应用。
    application = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        #Swagger UI，可直接在浏览器测试接口。
        docs_url="/docs",
        #ReDoc 文档，可直接在浏览器查看接口文档。
        redoc_url="/redoc",
        #OpenAPI JSON 文档，可直接在浏览器查看接口文档的 JSON 格式。
        openapi_url="/openapi.json",
    )
    #注册统一业务异常处理器
    application.add_exception_handler(ApplicationError, application_error_handler)
    #存活检查、数据库就绪检查
    application.include_router(health_router)
    #提交文本需求、上传 PDF/Word/图片、查询原始输入
    application.include_router(sources_router)
    #查询 AI 分析结果、重新分析、综合检索
    application.include_router(analysis_router)
    #查询审核任务、批准、驳回、退回
    application.include_router(reviews_router)
    #查询正式需求、需求版本、功能变更历史
    application.include_router(requirements_router)
    return application


app = create_app()
