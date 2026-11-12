import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter_ns

import numpy as np
import pytest
from prometheus_client.parser import text_string_to_metric_families

from mlops_lai.core.settings import Settings
from mlops_lai.domain.schemas import PedidoLAI
from mlops_lai.monitoring.telemetry import (
    WRITE_FAILURES,
    TelemetryRecorder,
    get_recorder,
    json_safe,
)
from mlops_lai.service import ScoringHandler

BASE = {
    "OrgaoDestinatario": "CC-PR – Casa Civil da Presidência da República",
    "DataRegistro": "15/09/2026",
}
HISTORY = {
    "n_pedidos_previos": 80,
    "prev_reenc_solicitante": 3,
    "prev_reenc_solicitante_den": 80,
    "n_pedidos_previos_neste_orgao": 12,
    "prev_reenc_neste_orgao": 0,
    "prev_reenc_neste_orgao_den": 12,
    "n_orgaos_distintos_previos": 14,
    "dias_desde_ultimo_pedido": 5,
}


def read_events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def scoring(tmp_path):
    service = ScoringHandler(Settings(telemetry_path=tmp_path / "scoring.jsonl"))
    yield service
    service.telemetry.close()


def test_features_match_actual_model_input_and_append_preserves_bytes(scoring, monkeypatch):
    received = []

    def predict(frame):
        received.append(frame.copy())
        return np.array([0.23456789])

    monkeypatch.setattr(scoring.model.booster, "predict", predict)
    response = scoring.score(PedidoLAI(**(BASE | HISTORY | {"DataNascimento": "10/01/1990"})))
    first_bytes = scoring.telemetry.path.read_bytes()
    event = read_events(scoring.telemetry.path)[0]
    assert len(received) == 1
    assert event["features"] == json_safe(received[0].iloc[0].to_dict())
    assert len(event["features"]) == 31
    assert event["response"] == response
    assert event["prediction_raw"] == 0.23456789
    assert event["input"]["prev_reenc_solicitante_den"] == 80
    assert event["quality"]["age_state"] == "valid"
    assert "DataNascimento" not in event["input"]
    scoring.score(PedidoLAI(**BASE))
    assert scoring.telemetry.path.read_bytes().startswith(first_bytes)
    assert len(read_events(scoring.telemetry.path)) == 2


def test_partial_history_keeps_input_and_records_effective_defaults(scoring):
    scoring.score(PedidoLAI(**(BASE | {"n_pedidos_previos": 0, "UF_sol": "ZZ"})))
    event = read_events(scoring.telemetry.path)[0]
    assert event["input"]["n_pedidos_previos"] == 0
    assert event["features"]["n_pedidos_previos"] == -1
    assert event["quality"]["historico_parcial_ignorado"]
    assert "UF_sol" in event["quality"]["unknown_categories"]
    assert "UF" in event["quality"]["missing_fields"]
    assert event["features"]["idade"] is None


def test_zero_denominator_baseline_and_model_exception(scoring, monkeypatch):
    scoring.score(PedidoLAI(**(BASE | HISTORY | {"prev_reenc_solicitante_den": 0})))
    scoring.score_baseline(PedidoLAI(**BASE))
    events = read_events(scoring.telemetry.path)
    assert events[0]["features"]["prev_reenc_rate_solicitante"] == -1
    assert events[1]["features"] is None
    assert events[1]["response"]["metodo"]

    def broken_predict(_):
        raise RuntimeError("erro sintético")

    monkeypatch.setattr(scoring.model.booster, "predict", broken_predict)
    with pytest.raises(RuntimeError):
        scoring.score(PedidoLAI(**BASE))
    failed = read_events(scoring.telemetry.path)[-1]
    assert failed["error"]["kind"] == "model_error"
    assert failed["error"]["stage"] == "inference"
    assert failed["response"] is None


def test_concurrent_operations_have_isolated_context_and_valid_json(tmp_path):
    recorder = TelemetryRecorder(True, tmp_path / "scoring.jsonl")

    def invoke(index):
        with recorder.operation("/score", BASE | {"n_pedidos_previos": index}) as event:
            event["response"] = {"index": index}

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(invoke, range(80)))
    events = read_events(recorder.path)
    recorder.close()
    assert len(events) == len({e["request_id"] for e in events}) == 80
    assert all(e["input"]["n_pedidos_previos"] == e["response"]["index"] for e in events)


def test_processes_and_restarts_preserve_existing_files(tmp_path):
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    environment = os.environ | {"PROMETHEUS_MULTIPROC_DIR": str(metrics_dir)}
    code = (
        "from pathlib import Path; from time import perf_counter_ns; "
        "from mlops_lai.monitoring.telemetry import TelemetryRecorder; "
        f"r=TelemetryRecorder(True, Path({str(tmp_path / 'scoring.jsonl')!r})); "
        "r.finish(r.new_event('/health', 'POST'), perf_counter_ns()); r.close()"
    )
    processes = [subprocess.Popen([sys.executable, "-c", code], env=environment) for _ in range(2)]
    assert all(p.wait(timeout=60) == 0 for p in processes)
    before = {p: p.read_bytes() for p in tmp_path.glob("*.jsonl")}
    subprocess.run([sys.executable, "-c", code], env=environment, check=True, timeout=60)
    assert len(list(tmp_path.glob("*.jsonl"))) == 3
    assert all(p.read_bytes() == content for p, content in before.items())
    assert all(len(read_events(p)) == 1 for p in tmp_path.glob("*.jsonl"))
    scrape_code = (
        "from prometheus_client import CollectorRegistry, generate_latest; "
        "from prometheus_client.multiprocess import MultiProcessCollector; "
        "r=CollectorRegistry(); MultiProcessCollector(r); print(generate_latest(r).decode())"
    )
    result = subprocess.run(
        [sys.executable, "-c", scrape_code],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    samples = [
        sample
        for family in text_string_to_metric_families(result.stdout)
        for sample in family.samples
    ]
    assert sum(s.value for s in samples if s.name == "lai_http_requests_total") == 3
    assert sum(s.value for s in samples if s.name == "lai_http_request_duration_seconds_count") == 3


def test_write_failure_is_visible_and_disabled_recorder_creates_no_files(tmp_path, monkeypatch):
    disabled = TelemetryRecorder(False, tmp_path / "disabled" / "scoring.jsonl")
    disabled.finish(disabled.new_event("/health"), perf_counter_ns())
    assert not disabled.path.parent.exists()
    recorder = TelemetryRecorder(True, tmp_path / "scoring.jsonl")

    def fail(*args, **kwargs):
        raise OSError("disco sinteticamente cheio")

    before = WRITE_FAILURES._value.get()
    monkeypatch.setattr(recorder.logger.handlers[0], "emit", fail)
    recorder.finish(recorder.new_event("/health"), perf_counter_ns())
    assert WRITE_FAILURES._value.get() == before + 1
    recorder.close()
    blocked = tmp_path / "blocked"
    blocked.write_text("arquivo", encoding="utf-8")
    with pytest.raises(OSError):
        TelemetryRecorder(True, blocked / "scoring.jsonl")


def test_recorder_reopens_after_application_lifecycle_closes_it(tmp_path):
    settings = Settings(telemetry_path=tmp_path / "scoring.jsonl")
    first = get_recorder(settings)
    first.finish(first.new_event("/health"), perf_counter_ns())
    first.close()
    second = get_recorder(settings)
    second.finish(second.new_event("/health"), perf_counter_ns())
    second.close()
    assert first.path != second.path
    assert len(read_events(first.path)) == len(read_events(second.path)) == 1
