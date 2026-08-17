# Connector Readiness Audit — every stage is one command, resumable, reproducible.
# See CLAUDE.md §6. `make run` is idempotent: it skips apps already in the run's
# JSONL with a valid schema, and never re-fetches a cached URL unless --refresh.

.DEFAULT_GOAL := help
RUN ?= latest
APP ?=
PY  := uv run python -m research.cli

.PHONY: help setup diff discover fetch extract verify score queue render build deploy run gold-sample test clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## uv sync (installs deps into .venv)
	uv sync --extra dev

diff: ## Pull Composio catalog and diff vs the 100 -> data/composio_catalog.json
	$(PY) diff

discover: ## search -> candidate doc URLs (per app)
	$(PY) discover --run $(RUN) $(if $(APP),--app $(APP),)

fetch: ## scrape + cache + write fetchlog.jsonl
	$(PY) fetch --run $(RUN) $(if $(APP),--app $(APP),)

extract: ## pass-1 constrained-JSON extraction -> pass1.jsonl
	$(PY) extract --run $(RUN) $(if $(APP),--app $(APP),)

verify: ## pass-2 verification loops -> pass2.jsonl + fixes.jsonl
	$(PY) verify --run $(RUN) $(if $(APP),--app $(APP),)

queue: ## gate classification -> queue.json (ops lanes)
	$(PY) queue --run $(RUN)

score: ## gold comparison -> accuracy.json (Wilson intervals, per stratum)
	$(PY) score --run $(RUN)

render: ## emit site/data.json + site/llms.txt + inject index.html
	$(PY) render --run $(RUN)

run: ## Full pipeline, resumable, all 100 (or one APP=<slug>)
	$(PY) run --run $(RUN) $(if $(APP),--app $(APP),)

build: render ## Alias: render the site
	@echo "site/ is ready"

gold-sample: ## Propose the 20-row stratified gold sample (HUMAN then verifies it)
	$(PY) gold-sample

deploy: build ## Push site/ to Cloudflare Pages (needs wrangler + CF_* env)
	npx --yes wrangler pages deploy site --project-name connector-readiness-audit

test: ## Run the test suite
	uv run pytest -q

clean: ## Remove run artifacts (keeps cache and final/)
	rm -rf data/runs/*
