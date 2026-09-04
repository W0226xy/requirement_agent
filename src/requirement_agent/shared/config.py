from functools import lru_cache

from pydantic import Field, model_validator
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

    llm_base_url: str
    llm_api_key: str
    llm_model: str
    embedding_base_url: str
    embedding_api_key: str
    embedding_model: str
    embedding_dimension: int = Field(gt=0)
    llm_timeout_seconds: float = Field(default=60, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=2)

    retrieval_keyword_weight: float = Field(default=0.4, ge=0, le=1)
    retrieval_vector_weight: float = Field(default=0.4, ge=0, le=1)
    retrieval_business_weight: float = Field(default=0.2, ge=0, le=1)
    retrieval_candidate_limit: int = Field(default=20, ge=10, le=20)

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
