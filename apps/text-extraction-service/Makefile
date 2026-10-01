.PHONY: check-autoformat autoformat lint debug-app test

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

build-docker:
	docker build \
	  --build-arg RUNENV=staging \
	  -t text-extraction-service:0.1.0 \
	  -f docker/Dockerfile .

run-docker:
	docker run -p 5000:5000 --env-file .env text-extraction-service:0.1.0
