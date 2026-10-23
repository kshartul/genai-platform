PROFILE ?= profiles/knowledge-assistant.local.yaml

.PHONY: install seed test lint eval run mcp ask chunk-bakeoff

install:
	pip install -e ".[dev]"

seed:
	python scripts/seed_warehouse.py

test: seed
	pytest -q

lint:
	ruff check src services tests

eval: seed
	genai eval --profile $(PROFILE) --report reports/eval.json

run: seed
	GENAI_PROFILE=$(PROFILE) uvicorn services.api.main:app --reload --port 8000

mcp: seed
	GENAI_PROFILE=$(PROFILE) python -m services.mcp_server.server

ask:
	genai ask --profile $(PROFILE) "$(Q)"

chunk-bakeoff: seed
	python scripts/chunk_bakeoff.py --profile $(PROFILE)
