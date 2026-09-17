from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "Requirement Agent API"
    app_version: str = "0.1.0"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"

    database_url: str
    redis_url: str
    celery_broker_url: str
    celery_result_backend: str

    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str = "requirement-agent"
    minio_secure: bool = False
    max_upload_size_bytes: int = Field(default=52_428_800, gt=0)

    feishu_app_id: str = ""
    feishu_app_secret: SecretStr = SecretStr("")
    feishu_verification_token: SecretStr = SecretStr("")
    feishu_encrypt_key: SecretStr = SecretStr("")
    feishu_base_url: str = "https://open.feishu.cn"
    feishu_timeout_seconds: float = Field(default=10, gt=0, le=60)

    llm_base_url: str
    llm_api_key: str
    llm_model: str
    embedding_base_url: str
    embedding_api_key: str
    embedding_model: str
    embedding_dimension: int = Field(gt=0)
    llm_timeout_seconds: float = Field(default=120, gt=0, le=600)
    llm_max_completion_tokens: int = Field(default=4096, ge=256, le=32_768)
    llm_max_retries: int = Field(default=2, ge=0, le=2)

    retrieval_keyword_weight: float = Field(default=0.4, ge=0, le=1)
    retrieval_vector_weight: float = Field(default=0.4, ge=0, le=1)
    retrieval_business_weight: float = Field(default=0.2, ge=0, le=1)
    retrieval_candidate_limit: int = Field(default=20, ge=10, le=20)
    retrieval_min_similarity_score: float = Field(default=0.40, ge=0, le=1)

    # The prompt contains the durable summary plus this sliding, uncompressed window.
    conversation_context_message_limit: int = Field(default=10, ge=1, le=20)
    conversation_context_recent_message_limit: int = Field(default=2, ge=1, le=20)#每次分析新消息时，除摘要外，还会保留最近 2 条未压缩原文消息。
    conversation_context_char_limit: int = Field(default=6_000, gt=0)#拼给模型的会话记忆总长度最多 6000 字符，避免 Prompt 无限增长。
    conversation_memory_summary_limit: int = Field(default=2_000, gt=0)#摘要最多 2000 字符。
    #满足以下任一条件时，会尝试触发摘要压缩：
    #1.同一会话消息总数达到 4 条；
    conversation_memory_compact_message_threshold: int = Field(default=4, ge=2)
    #2.同一会话消息总长度达到 12000 字符。
    conversation_memory_compact_char_threshold: int = Field(default=12_000, gt=0)

    @model_validator(mode="after")
    def validate_retrieval_weights(self) -> "Settings":
        total = (
            self.retrieval_keyword_weight
            + self.retrieval_vector_weight
            + self.retrieval_business_weight
        )
        if abs(total - 1.0) > 1e-9:
            raise ValueError("retrieval weights must sum to 1.0")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
