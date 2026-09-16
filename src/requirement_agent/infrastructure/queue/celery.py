from celery import Celery

from requirement_agent.shared.config import Settings, get_settings


def create_celery_app(settings: Settings | None = None) -> Celery:#工厂函数，用于创建 Celery 实例
    app_settings = settings or get_settings()#正常运行时：调用 get_settings()，从 .env 读取配置；测试用例：传入自定义 Settings 实例
    application = Celery(
        "requirement_agent",
        broker=app_settings.celery_broker_url,#Broker 是任务消息中间件。项目里通常配置为 Redis,负责存放待执行任务
        #例如 API 调用,任务会先写入 Redis，Worker 再从 Redis 中取出并执行

        backend=app_settings.celery_result_backend,#用于存储任务结果。
    )
    application.conf.update(
        task_serializer="json",#任务消息序列化格式为 JSON
        result_serializer="json",#任务结果序列化格式为 JSON
        accept_content=["json"],#Worker 只接受 JSON 格式消息
        timezone="UTC",#Worker 时区设置为 UTC
        enable_utc=True,
        task_acks_late=True,#Worker 执行任务时，只有在任务执行完成后才向 Broker 确认消息已处理。若 Worker 在执行任务过程中挂掉，Broker 会重新将任务放回队列，确保任务不会丢失。
        worker_prefetch_multiplier=1,
        task_reject_on_worker_lost=True,
        task_routes={#任务路由配置，指定不同任务发送到不同队列
            "requirement_agent.sources.*": {"queue": "source_processing"},
            "requirement_agent.requirements.*": {"queue": "source_processing"},
            "requirement_agent.conversations.*": {"queue": "source_processing"},
            "requirement_agent.connectors.feishu.*": {"queue": "source_processing"},
        },
    )
    return application
