.PHONY: install lint test validate serve test-1 test-2 docker-build docker-up

install:
	uv sync --extra dev

lint:
	uv run ruff check src tests

test:
	uv run pytest --cov=mlops_lai --cov-report=term-missing

validate:
	uv run lai-validate-artifact --artifact-dir artifacts/registered

serve:
	uv run bentoml serve mlops_lai.service:LaiTriagem --port 3000

test-1:
	curl -sS -X POST http://localhost:3000/score -H 'Content-Type: application/json' -d '{"pedido":{"OrgaoDestinatario":"CC-PR – Casa Civil da Presidência da República","DataRegistro":"15/09/2026","Escolaridade":"Ensino Fundamental","UF_sol":"PE","n_pedidos_previos":80,"prev_reenc_solicitante":3,"prev_reenc_solicitante_den":80,"n_pedidos_previos_neste_orgao":12,"prev_reenc_neste_orgao":0,"prev_reenc_neste_orgao_den":12,"n_orgaos_distintos_previos":14,"dias_desde_ultimo_pedido":5}}'

test-2:
	curl -sS -X POST http://localhost:3000/score_baseline -H 'Content-Type: application/json' -d '{"pedido":{"OrgaoDestinatario":"CC-PR – Casa Civil da Presidência da República","DataRegistro":"15/09/2026"}}'