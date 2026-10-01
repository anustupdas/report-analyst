.PHONY: check-autoformat autoformat lint debug-app test run build build-docker

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
	uvicorn text_extraction_service.app:app --reload --port 5000 --host 0.0.0.0 --log-level debug

run:
	@test -x .venv/bin/uvicorn || { echo "Missing .venv. From the repo root run: make install-extract"; exit 1; }
	.venv/bin/uvicorn text_extraction_service.app:app --host 0.0.0.0 --port 5000 --log-level info

build:
	docker build \
	  --build-arg RUNENV=staging \
	  -t text-extraction-service:0.1.0 \
	  -f docker/Dockerfile .

build-docker: build

run-docker:
	docker run -p 5000:5000 --env-file .env text-extraction-service:0.1.0
