FROM node:24-bookworm-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS app
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    CHANGELORG_DATA_DIR=/data \
    CHANGELORG_DB=/data/changelorg.duckdb \
    CHANGELORG_AUTH_ENABLED=false \
    CHANGELORG_OAUTH2_PROXY_URL=http://oauth2-proxy:4180 \
    CHANGELORG_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173 \
    CHANGELORG_AUTO_REFRESH=true \
    CHANGELORG_REFRESH_INTERVAL_SECONDS=3600 \
    CHANGELORG_REFRESH_WINDOW=30d \
    CHANGELORG_SEED_DEFAULT_SOURCES=true

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev

COPY --from=frontend /app/frontend/dist ./frontend/dist

# DuckDB requires one owner process. Revisit storage before any worker increase.
CMD ["/app/.venv/bin/uvicorn", "changelorg.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
