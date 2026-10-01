.PHONY: check-autoformat autoformat lint debug-app test run build

check-autoformat:
	python -m black --check .

autoformat:
	python -m black .

lint:
	python -m flake8 src tests
	python -m ruff check src tests

test:
	python -m pytest -q

debug-app:
	uvicorn embedding_service.app:app --reload --port 5100 --host 0.0.0.0 --log-level debug

run:
	@test -x .venv/bin/uvicorn || { echo "Missing .venv. From the repo root run: make install-embed"; exit 1; }
	.venv/bin/uvicorn embedding_service.app:app --host 0.0.0.0 --port 5100 --log-level info

build:
	docker build -t embedding-service:0.1.0 -f docker/Dockerfile .
