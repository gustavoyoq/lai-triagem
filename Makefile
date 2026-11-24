.PHONY: install lint test validate serve start test-1 test-2 test-3 load latency datasets new-monitor-run check-run serve-reference serve-production replay-reference replay-production drift-sets reproduce-av2 dashboard metrics monitor

PORT ?= 3000
DASHBOARD_PORT ?= 8501
RUN_DIR ?=
LATENCY_OUTPUT ?= reports/latency_percentiles.json
DRIFT_OUTPUT ?= $(RUN_DIR)/drift.json

install:
	uv sync --extra dev

lint:
	uv run ruff check src tests scripts

test:
	uv run pytest --cov=mlops_lai --cov-report=term-missing

validate:
	uv run lai-validate-artifact --artifact-dir artifacts/registered

serve:
	uv run bentoml serve mlops_lai.service:LaiTriagem --port $(PORT)

start: install validate serve

load:
	uv run python scripts/generate_load.py --requests 120 --concurrency 4

latency:
	uv run python scripts/latency_report.py --url "http://127.0.0.1:$(PORT)" --requests 100 --output "$(LATENCY_OUTPUT)"

datasets:
	uv run python scripts/build_monitor_sets.py

new-monitor-run:
	@mkdir -p monitoring/telemetry/runs
	@mktemp -d monitoring/telemetry/runs/av2-XXXXXXXX

check-run:
	@test -n "$(RUN_DIR)" && test -d "$(RUN_DIR)" || { echo 'Use RUN_DIR com o diretório criado por make new-monitor-run.' >&2; exit 1; }

serve-reference serve-production: serve-%: check-run
	MLOPS_ENABLE_TELEMETRY=true MLOPS_TELEMETRY_PATH="$(RUN_DIR)/$*/scoring.jsonl" uv run bentoml serve mlops_lai.service:LaiTriagem --host 127.0.0.1 --port $(PORT)

replay-reference replay-production: replay-%: check-run
	uv run python scripts/replay_inputs.py --url "http://127.0.0.1:$(PORT)" --inputs "data/$*/score_inputs.jsonl" --telemetry "$(RUN_DIR)/$*"

drift-sets: check-run
	uv run python scripts/monitor_shift.py --reference "$(RUN_DIR)/reference" --current "$(RUN_DIR)/production" --expected-count 100 --min-samples 100 --output "$(DRIFT_OUTPUT)"

reproduce-av2:
	@av2_output="$$($(MAKE) --no-print-directory new-monitor-run)" && \
		uv run --locked --extra dashboard python scripts/reproduce_av2.py --output "$$av2_output"

dashboard:
	uv run --extra dashboard streamlit run scripts/dashboard_av2.py \
		--server.address 127.0.0.1 --server.port $(DASHBOARD_PORT) --server.headless true \
		-- --run-dir "$(or $(RUN_DIR),reports/av2)"

metrics:
	curl -sS http://localhost:3000/metrics

monitor:
	docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up --build

test-1:
	curl -sS -X POST http://localhost:3000/score -H 'Content-Type: application/json' -d '{"pedido":{"OrgaoDestinatario":"CC-PR – Casa Civil da Presidência da República","DataRegistro":"15/09/2026","Escolaridade":"Ensino Fundamental","UF_sol":"PE","n_pedidos_previos":80,"prev_reenc_solicitante":3,"prev_reenc_solicitante_den":80,"n_pedidos_previos_neste_orgao":12,"prev_reenc_neste_orgao":0,"prev_reenc_neste_orgao_den":12,"n_orgaos_distintos_previos":14,"dias_desde_ultimo_pedido":5}}'

test-2:
	curl -sS -X POST http://localhost:3000/score -H 'Content-Type: application/json' -d '{"pedido": {"OrgaoDestinatario": "Prefeitura","DataRegistro": "15/09/2026"}}'

test-3:
	curl -sS -X POST http://localhost:3000/score -H 'Content-Type: application/json' -d '{"pedido": {"OrgaoDestinatario": "CC-PR – Casa Civil da Presidência da República","DataRegistro": "15/09/2026"}}'
