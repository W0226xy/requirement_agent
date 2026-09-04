import os

TEST_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
    "REDIS_URL": "redis://localhost:6379/0",
    "CELERY_BROKER_URL": "redis://localhost:6379/0",
    "CELERY_RESULT_BACKEND": "redis://localhost:6379/1",
    "MINIO_ENDPOINT": "localhost:9000",
    "MINIO_ACCESS_KEY": "test-access-key",
    "MINIO_SECRET_KEY": "test-secret-key",
    "LLM_BASE_URL": "https://llm.invalid/v1",
    "LLM_API_KEY": "test-api-key",
    "LLM_MODEL": "test-chat-model",
    "EMBEDDING_BASE_URL": "https://embedding.invalid/v1",
    "EMBEDDING_API_KEY": "test-embedding-api-key",
    "EMBEDDING_MODEL": "test-embedding-model",
    "EMBEDDING_DIMENSION": "3",
}

for key, value in TEST_ENV.items():
    os.environ.setdefault(key, value)
