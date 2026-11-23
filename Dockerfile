FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS python-build
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

FROM node:22-slim AS web-build
WORKDIR /web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web ./
RUN pnpm build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY --from=python-build /app/.venv /app/.venv
COPY --from=python-build /app/src /app/src
COPY --from=web-build /web/dist /app/web/dist
ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8000
CMD ["uvicorn", "ndis.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
