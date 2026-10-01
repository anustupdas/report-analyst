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
	uvicorn embedding_service.app:app --reload --port 5100 --host 0.0.0.0 --log-level debug
