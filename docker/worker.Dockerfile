FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip pip install -e ".[dev,parsers]"

COPY apps ./apps

USER app

CMD ["celery", "-A", "apps.worker.celery_app:celery_app", "worker", "--loglevel=INFO"]
