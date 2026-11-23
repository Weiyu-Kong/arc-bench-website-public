FROM python:3.11-slim AS build

ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY backend ./backend
COPY source.sh config.yaml ./
RUN uv sync --frozen --no-dev

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git nodejs npm && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app /app
COPY deploy/docker/previewer-entrypoint.sh /usr/local/bin/previewer-entrypoint.sh
RUN chmod +x /usr/local/bin/previewer-entrypoint.sh
ENTRYPOINT ["previewer-entrypoint.sh"]
