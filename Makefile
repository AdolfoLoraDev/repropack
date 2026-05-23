.PHONY: install test lint format clean build publish

install:
	pip install -e ".[dev]"
	pre-commit install

test:
	pytest -v --cov=src/repropack --cov-report=term-missing --cov-report=xml --cov-report=html

lint:
	ruff check src tests
	mypy src/repropack --strict

format:
	black src tests
	ruff check --fix src tests

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete

build:
	python -m build

publish:
	python -m twine upload dist/*
