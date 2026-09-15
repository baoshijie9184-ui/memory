FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY migrations ./migrations
RUN pip install --no-cache-dir ".[tokenizer]"

RUN useradd --system --uid 10001 --create-home appuser \
    && mkdir -p /data/sqlite /data/json-mirror \
    && chown -R appuser:appuser /data
USER appuser

EXPOSE 8000
CMD ["desaymem-api"]
