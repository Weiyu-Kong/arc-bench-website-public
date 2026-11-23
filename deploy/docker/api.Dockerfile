FROM python:3.11-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY backend ./backend
COPY data ./data
COPY assets ./assets
COPY packages ./packages
COPY reference-implementations ./reference-implementations
COPY runtime ./runtime
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./
COPY source.sh config.yaml ./
COPY arc-template ./arc-template
COPY reference-implementations ./reference-implementations
COPY dataset ./dataset

RUN uv sync --frozen --no-dev

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=build /opt/venv /opt/venv
COPY --from=build /app /app
COPY deploy/docker/api-entrypoint.sh /usr/local/bin/api-entrypoint.sh

RUN chmod +x /usr/local/bin/api-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["api-entrypoint.sh"]
CMD ["arcbench-api"]
