# One entry point for humans and CI. `make help` lists targets.
.DEFAULT_GOAL := help
UV ?= uv run
PORT ?= 2030  # 2026-2029 belong to the sibling LangGraph projects

.PHONY: help install dev dev-reload demo lint fmt test test-fast ui-test e2e eval live check clean

help:  ## show targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

install:  ## create the venv
	uv sync

dev:  ## run it (Studio-compatible). Tracing follows .env; TRACE=1 forces it on, NOTRACE=1 off. Open http://127.0.0.1:2030/app/
	RESUMABLE_STREAM_TTL_SECONDS=7200 $(if $(TRACE),LANGSMITH_TRACING=true) $(if $(NOTRACE),LANGSMITH_TRACING=false) $(UV) langgraph dev --port $(PORT) --no-browser --no-reload --n-jobs-per-worker 8

dev-reload:  ## like dev, with hot reload (restarts every live match on each edit)
	RESUMABLE_STREAM_TTL_SECONDS=7200 $(if $(TRACE),LANGSMITH_TRACING=true) $(if $(NOTRACE),LANGSMITH_TRACING=false) $(UV) langgraph dev --port $(PORT) --no-browser --n-jobs-per-worker 8

demo:  ## a whole match in the terminal, no server: the autonomous loop on compiled graphs (FIXTURE=derby MODE=autopilot)
	$(UV) python -m gantry.live.demo --fixture $(or $(FIXTURE),derby) --mode $(or $(MODE),autopilot)

lint:  ## ruff
	$(UV) ruff check src tests

fmt:  ## ruff autofix + format
	$(UV) ruff check --fix src tests
	$(UV) ruff format src tests

test-fast:  ## everything except e2e and live
	$(UV) pytest -m "not e2e and not live" -q

test: test-fast ui-test  ## python + node tests

ui-test:  ## node --test for the pure JS modules
	node --test tests/ui/*.test.mjs

e2e:  ## spawns a real langgraph dev on a free port (offline)
	$(UV) pytest -m e2e -q

eval:  ## offline eval gate: curation vs hidden ground truth, autonomy invariants, memory lift
	$(UV) pytest tests/evals -q -s

live:  ## LIVE, opt-in, costs cents: one window on a real model
	$(UV) pytest -m live tests/e2e -s -q

check: lint test e2e  ## what CI runs

clean:
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov .langgraph_api var
