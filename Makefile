# Build & run (SPEC §3.9)
#   make dev    backend on :8099 with DEMO_MODE=true, vite dev server proxied
#   make build  docker build
#   make demo   docker compose --profile demo up   (DEMO_MODE=true)
#   make up     docker compose up                  (real scan)

SHELL := /bin/bash
PORT ?= 8099
VENV := backend/.venv
PY := $(VENV)/bin/python

.PHONY: dev build demo up down test clean

dev: $(VENV) frontend/node_modules
	@mkdir -p data
	@echo "backend  → http://127.0.0.1:$(PORT)  (DEMO_MODE=true)"
	@echo "frontend → http://127.0.0.1:5173     (proxies /api to the backend)"
	@bash -c 'set -m; \
	  DEMO_MODE=true DATA_DIR=$(CURDIR)/data PORT=$(PORT) \
	    $(CURDIR)/$(PY) -m uvicorn app.main:app --app-dir backend --reload --port $(PORT) & \
	  backend_pid=$$!; \
	  trap "kill $$backend_pid 2>/dev/null" EXIT INT TERM; \
	  cd frontend && BACKEND_URL=http://127.0.0.1:$(PORT) npm run dev'

build:
	docker compose build

demo:
	docker compose --profile demo up

up:
	docker compose up

down:
	docker compose --profile demo --profile live down

test: $(VENV)
	cd backend && ../$(VENV)/bin/python -m pytest -q
	cd frontend && npx tsc -b

clean:
	rm -rf $(VENV) frontend/node_modules frontend/dist

$(VENV): backend/requirements-dev.txt
	python3.12 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -r backend/requirements-dev.txt
	@touch $(VENV)

frontend/node_modules: frontend/package-lock.json
	cd frontend && npm ci --no-audit --no-fund
	@touch frontend/node_modules
