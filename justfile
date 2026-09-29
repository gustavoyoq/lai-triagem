set shell := ["powershell.exe", "-NoProfile", "-Command"]

install:
    uv sync --extra dev

check:
    uv run ruff check src tests
    uv run pytest

validate:
    uv run lai-validate-artifact --artifact-dir artifacts/registered

serve:
    uv run bentoml serve mlops_lai.service:LaiTriagem --port 3000

build-bento:
    uv run bentoml build

docker:
    docker compose up --build
