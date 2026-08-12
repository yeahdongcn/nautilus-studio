.PHONY: lint test web-build web-audit check

VENV ?= .venv
PYTHON := $(VENV)/bin/python
RUFF := $(VENV)/bin/ruff

lint:
	$(RUFF) format --check src tests scripts/build-continuation-comparison.py scripts/probe-image-edit.py scripts/probe-h3-continuation.py scripts/verify-qwen-image-edit-checkpoint.py
	$(RUFF) check src tests scripts/build-continuation-comparison.py scripts/probe-image-edit.py scripts/probe-h3-continuation.py scripts/verify-qwen-image-edit-checkpoint.py
	node --check src/long_video_studio/static/app.js

test:
	$(PYTHON) -m pytest -q

web-build:
	cd web && npm ci && npm run format && npm run build

web-audit:
	cd web && npm audit --audit-level=high

check: lint test web-build web-audit
