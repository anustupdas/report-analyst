.PHONY: check-autoformat autoformat lint test

check-autoformat:
	python -m black --check src tests

autoformat:
	python -m black src tests

lint:
	python -m flake8 src tests
	python -m ruff check src tests

test:
	python -m pytest -q
