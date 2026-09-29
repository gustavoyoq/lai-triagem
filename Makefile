.PHONY: install lint test validate serve docker-build docker-up

install:
	uv sync --extra dev

lint:
	ruff check src tests

test:
	pytest --cov=mlops_lai --cov-report=term-missing

validate:
	lai-validate-artifact --artifact-dir artifacts/registered

serve:
	uv run bentoml serve mlops_lai.service:LaiTriagem --port 3000

docker-build:
	docker compose build

docker-up:
	docker compose up --build
