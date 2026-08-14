# Membrane — common tasks.
# Windows users: every target here also works under Git Bash.

PY := apps/api/.venv/Scripts/python.exe
ifeq (,$(wildcard apps/api/.venv/Scripts/python.exe))
PY := apps/api/.venv/bin/python
endif

.PHONY: help setup api web bench bench-publish test test-api test-bench lint up down clean

help:
	@echo "make setup          create the venv and install everything"
	@echo "make api            run the proxy on :8080"
	@echo "make web            run the dashboard on :3000"
	@echo "make test           run every test suite"
	@echo "make bench          run InjectBench and print the report"
	@echo "make bench-publish  run InjectBench and post it to the leaderboard"
	@echo "make up / down      the whole stack under docker compose"

setup:
	python -m venv apps/api/.venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r apps/api/requirements.txt
	$(PY) -m pip install pytest pytest-asyncio
	cd apps/web && npm install

api:
	cd apps/api && .venv/Scripts/python.exe -m uvicorn membrane.main:app --reload --port 8080 \
	  || .venv/bin/python -m uvicorn membrane.main:app --reload --port 8080

web:
	cd apps/web && npm run dev

test: test-api test-bench

test-api:
	cd apps/api && ../../$(PY) -m pytest -q

test-bench:
	cd apps/bench && ../../$(PY) -m pytest -q

bench:
	cd apps/bench && ../../$(PY) -m injectbench --label local

bench-publish:
	cd apps/bench && ../../$(PY) -m injectbench --label local \
	  --json ../../docs/injectbench-results.json --publish http://localhost:8080

up:
	docker compose up --build

down:
	docker compose down -v

clean:
	rm -f apps/api/*.db apps/bench/*.db
	rm -rf apps/web/.next
