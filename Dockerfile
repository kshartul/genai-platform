FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[aws,azure,parsing,observability]" awslambdaric azure-storage-queue azure-storage-blob

COPY services ./services
COPY profiles ./profiles
COPY ontology ./ontology
COPY evals ./evals
COPY data ./data
COPY scripts ./scripts
RUN python scripts/seed_warehouse.py && useradd -r -u 10001 app && chown -R app /app
USER app

EXPOSE 8000 8001
# Default: API. ECS/Container Apps override the command for the MCP server and ingest worker;
# Lambda uses: python -m awslambdaric services.ingest_worker.handlers.aws_handler
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
