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
    RIFFLE_DATA_DIR=/data \
    RIFFLE_DB=/data/riffle.duckdb \
    RIFFLE_AUTH_ENABLED=false \
    RIFFLE_OAUTH2_PROXY_URL=http://oauth2-proxy:4180 \
    RIFFLE_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173 \
    RIFFLE_AUTO_REFRESH=true \
    RIFFLE_REFRESH_INTERVAL_SECONDS=3600 \
    RIFFLE_REFRESH_WINDOW=30d \
    RIFFLE_SEED_DEFAULT_SOURCES=true

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev

COPY --from=frontend /app/frontend/dist ./frontend/dist

# DuckDB requires one owner process. Revisit storage before any worker increase.
CMD ["/app/.venv/bin/uvicorn", "riffle.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
