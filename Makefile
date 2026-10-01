.PHONY: help setup run stop status docker-build docker-up docker-down \
	install install-extract install-api install-embed install-langgraph \
	db-up db-down db-reset db-migrate db-seed db-psql db-status \
	extract-up extract-down embed-up embed-down api-up api-down \
	langgraph-up langgraph-down \
	lint format test langfuse-upload

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
EXTRACT_DIR := $(ROOT)/apps/text-extraction-service
EMBED_DIR := $(ROOT)/apps/embedding-service
API_DIR := $(ROOT)/apps/api
LANGGRAPH_DIR := $(ROOT)/apps/langgraph-server
DATA_DIR := $(ROOT)/data
LOG_DIR := $(ROOT)/.run
EXTRACT_PID := $(LOG_DIR)/text-extraction.pid
EXTRACT_LOG := $(LOG_DIR)/text-extraction.log
EMBED_PID := $(LOG_DIR)/embedding.pid
EMBED_LOG := $(LOG_DIR)/embedding.log
API_PID := $(LOG_DIR)/api.pid
API_LOG := $(LOG_DIR)/api.log
LANGGRAPH_PID := $(LOG_DIR)/langgraph.pid
LANGGRAPH_LOG := $(LOG_DIR)/langgraph.log

ifneq (,$(wildcard $(ROOT)/.env))
include $(ROOT)/.env
export
endif

# Non-secret settings live in each service's configs/staging/config.yml; .env holds secrets only.
EXTRACT_CONFIG := $(EXTRACT_DIR)/configs/staging/config.yml
EMBED_CONFIG := $(EMBED_DIR)/configs/staging/config.yml
API_CONFIG := $(API_DIR)/configs/staging/config.yml
LANGGRAPH_CONFIG := $(LANGGRAPH_DIR)/configs/staging/config.yml
yaml_value = $(strip $(shell sed -n 's/^$(2):[[:space:]]*//p' "$(1)" 2>/dev/null | head -n 1))

TEXT_EXTRACTION_PORT := $(or $(call yaml_value,$(EXTRACT_CONFIG),textextractionservice.port),5000)
EMBEDDING_PORT := $(or $(call yaml_value,$(EMBED_CONFIG),embeddingservice.port),5100)
API_PORT := $(or $(call yaml_value,$(API_CONFIG),api.port),8000)
LANGGRAPH_PORT := $(or $(call yaml_value,$(LANGGRAPH_CONFIG),langgraph.port),8080)
POSTGRES_PORT := $(or $(call yaml_value,$(API_CONFIG),database.port),5432)
POSTGRES_DB := $(or $(call yaml_value,$(API_CONFIG),database.name),chat_project)
POSTGRES_USER ?= chat

help:
	@echo "Annual Report Analyst (local stack)"
	@echo "  make setup     Install deps for all apps + start DB + migrate"
	@echo "  make run       Start all services (Postgres + extraction + embedding + API + LangGraph)"
	@echo "  make stop      Stop app services (Postgres kept running)"
	@echo "  make status    Show DB + app process status"
	@echo "  make db-down   Stop Postgres (keep volume)"
	@echo "  make db-reset  Stop Postgres and DELETE volume"
	@echo "  make db-seed   Optional demo user/project"
	@echo "  make lint      flake8 + ruff (api + extraction + embedding + langgraph)"
	@echo "  make format    black (api + extraction + embedding + langgraph)"
	@echo "  make test      pytest (api + extraction + embedding + langgraph)"
	@echo "  make langfuse-upload  Push local fallback prompts to Langfuse"
	@echo "  make docker-build Build all service images"
	@echo "  make docker-up   Build if needed and start the whole stack"
	@echo "  make docker-down Stop the stack (volumes kept)"

setup: install db-up db-migrate
	@mkdir -p "$(DATA_DIR)"
	@echo
	@echo "Setup complete."
	@echo "  Postgres        localhost:$(POSTGRES_PORT)/$(POSTGRES_DB)"
	@echo "  Data root       $(DATA_DIR)"
	@echo "  text-extraction http://localhost:$(TEXT_EXTRACTION_PORT)"
	@echo "  embedding       http://localhost:$(EMBEDDING_PORT)"
	@echo "  API + UI        http://localhost:$(API_PORT)/"
	@echo "  langgraph       http://localhost:$(LANGGRAPH_PORT)"
	@echo "Settings: apps/*/configs/staging/config.yml  |  Secrets: .env files"
	@echo "Start services with: make run"

install:
	@test -f "$(ROOT)/.env" || cp "$(ROOT)/.env.example" "$(ROOT)/.env"
	@mkdir -p "$(DATA_DIR)"
	$(MAKE) install-extract
	$(MAKE) install-embed
	$(MAKE) install-api
	$(MAKE) install-langgraph

install-extract:
	@command -v uv >/dev/null 2>&1 || { \
		echo "ERROR: uv is required. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"; \
		exit 1; \
	}
	@test -f "$(EXTRACT_DIR)/.env" || cp "$(EXTRACT_DIR)/.env.example" "$(EXTRACT_DIR)/.env"
	@echo "Installing text-extraction-service deps..."
	@cd "$(EXTRACT_DIR)" && \
		if [ ! -x .venv/bin/python ]; then uv venv .venv --python 3.12; fi && \
		uv pip install -e ".[dev]" --python .venv/bin/python
	@echo "text-extraction-service ready."

install-embed:
	@command -v uv >/dev/null 2>&1 || { \
		echo "ERROR: uv is required. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"; \
		exit 1; \
	}
	@test -f "$(EMBED_DIR)/.env" || cp "$(EMBED_DIR)/.env.example" "$(EMBED_DIR)/.env"
	@echo "Installing embedding-service deps (includes torch; first run is slow)..."
	@cd "$(EMBED_DIR)" && \
		if [ ! -x .venv/bin/python ]; then uv venv .venv --python 3.12; fi && \
		uv pip install -e ".[dev]" --python .venv/bin/python
	@echo "embedding-service ready."

install-api:
	@command -v uv >/dev/null 2>&1 || { \
		echo "ERROR: uv is required. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"; \
		exit 1; \
	}
	@test -f "$(API_DIR)/.env" || cp "$(API_DIR)/.env.example" "$(API_DIR)/.env"
	@echo "Installing chat-api deps..."
	@cd "$(API_DIR)" && \
		if [ ! -x .venv/bin/python ]; then uv venv .venv --python 3.12; fi && \
		uv pip install -e ".[dev]" --python .venv/bin/python
	@echo "chat-api ready."

install-langgraph:
	@command -v uv >/dev/null 2>&1 || { \
		echo "ERROR: uv is required. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"; \
		exit 1; \
	}
	@test -f "$(LANGGRAPH_DIR)/.env" || cp "$(LANGGRAPH_DIR)/.env.example" "$(LANGGRAPH_DIR)/.env"
	@echo "Installing langgraph-server deps..."
	@cd "$(LANGGRAPH_DIR)" && \
		if [ ! -x .venv/bin/python ]; then uv venv .venv --python 3.12; fi && \
		uv pip install -e ".[dev]" --python .venv/bin/python
	@echo "langgraph-server ready."

docker-build:
	docker compose --env-file .env build

docker-up:
	@test -f .env || cp .env.example .env
	docker compose --env-file .env up --build

docker-down:
	docker compose --env-file .env down

run: db-up extract-up embed-up api-up langgraph-up
	@echo
	@echo "Services running:"
	@echo "  Postgres            → localhost:$(POSTGRES_PORT)"
	@echo "  text-extraction     → http://localhost:$(TEXT_EXTRACTION_PORT)  (logs: $(EXTRACT_LOG))"
	@echo "  embedding           → http://localhost:$(EMBEDDING_PORT)  (logs: $(EMBED_LOG))"
	@echo "  chat-api            → http://localhost:$(API_PORT)  (logs: $(API_LOG))"
	@echo "  langgraph           → http://localhost:$(LANGGRAPH_PORT)  (logs: $(LANGGRAPH_LOG))"
	@echo "  UI                  → http://localhost:$(API_PORT)/"
	@echo "  API docs            → http://localhost:$(API_PORT)/docs"
	@echo "Stop app services with: make stop"

stop: langgraph-down api-down embed-down extract-down
	@echo "App services stopped. Postgres still running (make db-down to stop it)."

status:
	@echo "=== Docker ==="
	@docker compose -f "$(ROOT)/docker-compose.yml" ps || true
	@echo
	@echo "=== App processes ==="
	@if [ -f "$(EXTRACT_PID)" ] && kill -0 $$(cat "$(EXTRACT_PID)") 2>/dev/null; then \
		echo "text-extraction: running pid=$$(cat "$(EXTRACT_PID)") port=$(TEXT_EXTRACTION_PORT)"; \
	else \
		echo "text-extraction: stopped"; \
	fi
	@if [ -f "$(EMBED_PID)" ] && kill -0 $$(cat "$(EMBED_PID)") 2>/dev/null; then \
		echo "embedding:       running pid=$$(cat "$(EMBED_PID)") port=$(EMBEDDING_PORT)"; \
	else \
		echo "embedding:       stopped"; \
	fi
	@if [ -f "$(API_PID)" ] && kill -0 $$(cat "$(API_PID)") 2>/dev/null; then \
		echo "chat-api:         running pid=$$(cat "$(API_PID)") port=$(API_PORT)"; \
	else \
		echo "chat-api:         stopped"; \
	fi
	@if [ -f "$(LANGGRAPH_PID)" ] && kill -0 $$(cat "$(LANGGRAPH_PID)") 2>/dev/null; then \
		echo "langgraph:        running pid=$$(cat "$(LANGGRAPH_PID)") port=$(LANGGRAPH_PORT)"; \
	else \
		echo "langgraph:        stopped"; \
	fi

db-up:
	@test -f "$(ROOT)/.env" || cp "$(ROOT)/.env.example" "$(ROOT)/.env"
	@docker compose -f "$(ROOT)/docker-compose.yml" up -d postgres
	@echo "Waiting for Postgres..."
	@for i in $$(seq 1 40); do \
		if docker compose -f "$(ROOT)/docker-compose.yml" exec -T postgres \
			pg_isready -U $(POSTGRES_USER) -d $(POSTGRES_DB) >/dev/null 2>&1; then \
			echo "Postgres is ready."; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "Postgres did not become ready in time."; \
	exit 1

db-down:
	docker compose -f "$(ROOT)/docker-compose.yml" stop postgres

db-reset:
	docker compose -f "$(ROOT)/docker-compose.yml" down -v
	@echo "Postgres volume removed. Run: make setup"

db-migrate:
	@chmod +x "$(ROOT)/db/migrate.sh"
	"$(ROOT)/db/migrate.sh"

db-seed:
	@chmod +x "$(ROOT)/db/seed.sh"
	"$(ROOT)/db/seed.sh"

db-psql:
	docker compose -f "$(ROOT)/docker-compose.yml" exec postgres \
		psql -U $(POSTGRES_USER) -d $(POSTGRES_DB)

db-status:
	docker compose -f "$(ROOT)/docker-compose.yml" ps
	@echo
	@docker compose -f "$(ROOT)/docker-compose.yml" exec -T postgres \
		psql -U $(POSTGRES_USER) -d $(POSTGRES_DB) \
		-c "SELECT version, applied_at FROM schema_migrations ORDER BY version;"

extract-up:
	@mkdir -p "$(LOG_DIR)" "$(DATA_DIR)"
	@if [ -f "$(EXTRACT_PID)" ] && kill -0 $$(cat "$(EXTRACT_PID)") 2>/dev/null; then \
		echo "text-extraction already running pid=$$(cat "$(EXTRACT_PID)")"; \
	else \
		test -x "$(EXTRACT_DIR)/.venv/bin/uvicorn" || $(MAKE) install-extract; \
		echo "Starting text-extraction on :$(TEXT_EXTRACTION_PORT) ..."; \
		cd "$(EXTRACT_DIR)" && \
			nohup .venv/bin/uvicorn text_extraction_service.app:app \
				--host 0.0.0.0 --port "$(TEXT_EXTRACTION_PORT)" --log-level info \
				> "$(EXTRACT_LOG)" 2>&1 & echo $$! > "$(EXTRACT_PID)"; \
		sleep 1; \
		if kill -0 $$(cat "$(EXTRACT_PID)") 2>/dev/null; then \
			echo "text-extraction started pid=$$(cat "$(EXTRACT_PID)")"; \
		else \
			echo "ERROR: text-extraction failed to start. See $(EXTRACT_LOG)"; \
			exit 1; \
		fi; \
	fi

extract-down:
	@if [ -f "$(EXTRACT_PID)" ]; then \
		pid=$$(cat "$(EXTRACT_PID)"); \
		if kill -0 $$pid 2>/dev/null; then \
			kill $$pid && echo "Stopped text-extraction pid=$$pid"; \
		fi; \
		rm -f "$(EXTRACT_PID)"; \
	else \
		echo "text-extraction was not running"; \
	fi

embed-up:
	@mkdir -p "$(LOG_DIR)"
	@if [ -f "$(EMBED_PID)" ] && kill -0 $$(cat "$(EMBED_PID)") 2>/dev/null; then \
		echo "embedding already running pid=$$(cat "$(EMBED_PID)")"; \
	else \
		test -x "$(EMBED_DIR)/.venv/bin/uvicorn" || $(MAKE) install-embed; \
		echo "Starting embedding-service on :$(EMBEDDING_PORT) ..."; \
		cd "$(EMBED_DIR)" && \
			nohup .venv/bin/uvicorn embedding_service.app:app \
				--host 0.0.0.0 --port "$(EMBEDDING_PORT)" --log-level info \
				> "$(EMBED_LOG)" 2>&1 & echo $$! > "$(EMBED_PID)"; \
		sleep 1; \
		if kill -0 $$(cat "$(EMBED_PID)") 2>/dev/null; then \
			echo "embedding started pid=$$(cat "$(EMBED_PID)")"; \
		else \
			echo "ERROR: embedding failed to start. See $(EMBED_LOG)"; \
			exit 1; \
		fi; \
	fi

embed-down:
	@if [ -f "$(EMBED_PID)" ]; then \
		pid=$$(cat "$(EMBED_PID)"); \
		if kill -0 $$pid 2>/dev/null; then \
			kill $$pid && echo "Stopped embedding pid=$$pid"; \
		fi; \
		rm -f "$(EMBED_PID)"; \
	else \
		echo "embedding was not running"; \
	fi

api-up:
	@mkdir -p "$(LOG_DIR)" "$(DATA_DIR)"
	@if [ -f "$(API_PID)" ] && kill -0 $$(cat "$(API_PID)") 2>/dev/null; then \
		echo "chat-api already running pid=$$(cat "$(API_PID)")"; \
	else \
		test -x "$(API_DIR)/.venv/bin/uvicorn" || $(MAKE) install-api; \
		echo "Starting chat-api on :$(API_PORT) ..."; \
		cd "$(API_DIR)" && \
			nohup .venv/bin/uvicorn chat_api.main:app \
				--host 0.0.0.0 --port "$(API_PORT)" --log-level info \
				> "$(API_LOG)" 2>&1 & echo $$! > "$(API_PID)"; \
		sleep 1; \
		if kill -0 $$(cat "$(API_PID)") 2>/dev/null; then \
			echo "chat-api started pid=$$(cat "$(API_PID)")"; \
		else \
			echo "ERROR: chat-api failed to start. See $(API_LOG)"; \
			exit 1; \
		fi; \
	fi

api-down:
	@if [ -f "$(API_PID)" ]; then \
		pid=$$(cat "$(API_PID)"); \
		if kill -0 $$pid 2>/dev/null; then \
			kill $$pid && echo "Stopped chat-api pid=$$pid"; \
		fi; \
		rm -f "$(API_PID)"; \
	else \
		echo "chat-api was not running"; \
	fi

langgraph-up:
	@mkdir -p "$(LOG_DIR)"
	@if [ -f "$(LANGGRAPH_PID)" ] && kill -0 $$(cat "$(LANGGRAPH_PID)") 2>/dev/null; then \
		echo "langgraph already running pid=$$(cat "$(LANGGRAPH_PID)")"; \
	else \
		test -x "$(LANGGRAPH_DIR)/.venv/bin/uvicorn" || $(MAKE) install-langgraph; \
		echo "Starting langgraph-server on :$(LANGGRAPH_PORT) ..."; \
		cd "$(LANGGRAPH_DIR)" && \
			nohup .venv/bin/uvicorn langgraph_server.main:app \
				--host 0.0.0.0 --port "$(LANGGRAPH_PORT)" --log-level info \
				> "$(LANGGRAPH_LOG)" 2>&1 & echo $$! > "$(LANGGRAPH_PID)"; \
		sleep 1; \
		if kill -0 $$(cat "$(LANGGRAPH_PID)") 2>/dev/null; then \
			echo "langgraph started pid=$$(cat "$(LANGGRAPH_PID)")"; \
		else \
			echo "ERROR: langgraph failed to start. See $(LANGGRAPH_LOG)"; \
			exit 1; \
		fi; \
	fi

langgraph-down:
	@if [ -f "$(LANGGRAPH_PID)" ]; then \
		pid=$$(cat "$(LANGGRAPH_PID)"); \
		if kill -0 $$pid 2>/dev/null; then \
			kill $$pid && echo "Stopped langgraph pid=$$pid"; \
		fi; \
		rm -f "$(LANGGRAPH_PID)"; \
	else \
		echo "langgraph was not running"; \
	fi

lint:
	@echo "=== chat-api lint ==="
	@cd "$(API_DIR)" && .venv/bin/python -m flake8 src tests && .venv/bin/python -m ruff check src tests
	@echo "=== text-extraction lint ==="
	@cd "$(EXTRACT_DIR)" && .venv/bin/python -m flake8 src tests && .venv/bin/python -m ruff check src tests
	@echo "=== embedding lint ==="
	@cd "$(EMBED_DIR)" && .venv/bin/python -m flake8 src tests && .venv/bin/python -m ruff check src tests
	@echo "=== langgraph lint ==="
	@cd "$(LANGGRAPH_DIR)" && .venv/bin/python -m flake8 src tests && .venv/bin/python -m ruff check src tests

format:
	@echo "=== chat-api format ==="
	@cd "$(API_DIR)" && .venv/bin/python -m black src tests
	@echo "=== text-extraction format ==="
	@cd "$(EXTRACT_DIR)" && .venv/bin/python -m black src tests
	@echo "=== embedding format ==="
	@cd "$(EMBED_DIR)" && .venv/bin/python -m black src tests
	@echo "=== langgraph format ==="
	@cd "$(LANGGRAPH_DIR)" && .venv/bin/python -m black src tests

test:
	@echo "=== chat-api tests ==="
	@cd "$(API_DIR)" && .venv/bin/python -m pytest -q
	@echo "=== text-extraction tests ==="
	@cd "$(EXTRACT_DIR)" && .venv/bin/python -m pytest -q
	@echo "=== embedding tests ==="
	@cd "$(EMBED_DIR)" && .venv/bin/python -m pytest -q
	@echo "=== langgraph tests ==="
	@cd "$(LANGGRAPH_DIR)" && .venv/bin/python -m pytest -q

langfuse-upload:
	@echo "=== upload API prompts ==="
	@cd "$(API_DIR)" && .venv/bin/python -m chat_api.prompt.upload
	@echo "=== upload LangGraph prompts ==="
	@cd "$(LANGGRAPH_DIR)" && .venv/bin/python -m langgraph_server.prompt.upload
