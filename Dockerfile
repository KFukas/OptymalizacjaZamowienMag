# Obraz usług Pythona (WMS API, mcp-wms, optymalizator, symulator, orkiestrator) — jeden obraz, różne komendy.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY wms ./wms
COPY optimizer ./optimizer
COPY simulator ./simulator
COPY mcp_wms ./mcp_wms
COPY orchestrator ./orchestrator
COPY evals ./evals
RUN pip install --upgrade pip && pip install -e .

COPY alembic.ini ./
COPY config ./config
COPY .claude/skills ./.claude/skills
COPY docker/entrypoint-wms.sh ./docker/entrypoint-wms.sh

RUN useradd --create-home --uid 10001 app && mkdir -p /app/var && chown -R app /app/var
USER app
EXPOSE 8000
CMD ["sh", "docker/entrypoint-wms.sh"]
