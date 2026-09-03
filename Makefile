# BuyBack Purchase Engine - developer tasks
# `make help` lists targets. Everything runs through `python -m` so it always
# uses the active interpreter (venv or uv), never a stray PATH entry.

PY ?= python

.DEFAULT_GOAL := help
.PHONY: help install lint format format-check typecheck test cov check run golden clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Editable install with dev extras + pre-commit hooks
	$(PY) -m pip install -e ".[dev]"
	$(PY) -m pre_commit install

lint:  ## Ruff lint (no changes)
	$(PY) -m ruff check src tests

format:  ## Ruff import-fix + format (writes)
	$(PY) -m ruff check --fix src tests
	$(PY) -m ruff format src tests

typecheck:  ## mypy (strict on src/)
	$(PY) -m mypy

test:  ## Run the test suite with coverage
	$(PY) -m pytest

cov:  ## Test suite with an HTML coverage report
	$(PY) -m pytest --cov-report=html
	@echo "open htmlcov/index.html"

check: lint format-check typecheck test  ## Everything CI runs

format-check:  ## Ruff format check (no writes)
	$(PY) -m ruff format --check src tests

run:  ## Sample run against the default workbook (BUDGET overridable)
	$(PY) -m purchase_engine --budget $(or $(BUDGET),1500)

golden:  ## Regenerate the golden fixture (after an intended behaviour change)
	PE_WRITE_GOLDEN=1 $(PY) -m pytest tests/golden --no-cov -q

clean:  ## Remove caches and build artefacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml \
	       dist build src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
