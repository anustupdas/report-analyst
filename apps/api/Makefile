.PHONY: check-autoformat autoformat lint test run build

check-autoformat:
	python -m black --check src tests

autoformat:
	python -m black src tests

lint:
	python -m flake8 src tests
	python -m ruff check src tests

test:
	python -m pytest -q

run:
	@test -x .venv/bin/uvicorn || { echo "Missing .venv. From the repo root run: make install-api"; exit 1; }
	.venv/bin/uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --log-level info

build:
	docker build -t chat-api:0.1.0 -f docker/Dockerfile ../..
