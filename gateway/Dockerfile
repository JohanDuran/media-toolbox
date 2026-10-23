# syntax=docker/dockerfile:1.7
# Gateway: dashboard + REST API + MCP endpoint. Build context is the repo root.

FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY gateway/web/package.json gateway/web/package-lock.json ./
RUN npm ci
COPY gateway/web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv
COPY gateway/requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/uv uv pip install --system -r /tmp/requirements.txt
WORKDIR /app
COPY gateway/app /app/app
COPY workers/common/shorts_spec.py /app/shorts_spec.py
COPY --from=web /web/dist /app/web
EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/api/state', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765", "--log-level", "warning"]
