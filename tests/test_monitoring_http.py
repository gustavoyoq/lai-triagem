import asyncio
import json

import pytest
from prometheus_client import CollectorRegistry
from prometheus_client.parser import text_string_to_metric_families
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from mlops_lai.core.settings import get_settings
from mlops_lai.monitoring.middleware import TelemetryMiddleware
from mlops_lai.monitoring.telemetry import get_recorder
from mlops_lai.service import LaiTriagem

BASE = {
    "OrgaoDestinatario": "CC-PR – Casa Civil da Presidência da República",
    "DataRegistro": "15/09/2026",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    from bentoml._internal.configuration.containers import BentoMLContainer
    from bentoml._internal.server.metrics.prometheus import PrometheusClient

    monkeypatch.setenv("MLOPS_TELEMETRY_PATH", str(tmp_path / "scoring.jsonl"))
    monkeypatch.setenv("MLOPS_ENABLE_TELEMETRY", "true")
    get_settings.cache_clear()
    previous = BentoMLContainer.metrics_client.get()
    native_metrics = PrometheusClient(multiproc=False)
    native_metrics._registry = CollectorRegistry()
    BentoMLContainer.metrics_client.set(native_metrics)
    try:
        with TestClient(LaiTriagem.to_asgi()) as http:
            yield http, get_recorder()
    finally:
        BentoMLContainer.metrics_client.set(previous)
        get_settings.cache_clear()


@pytest.mark.parametrize(
    "route,payload",
    [
        ("/score", {"pedido": BASE}),
        ("/score_baseline", {"pedido": BASE}),
        ("/score", {"pedido": BASE | {"DataRegistro": "31/02/2026"}}),
        ("/score", {"pedido": BASE | {"n_pedidos_previos": "inválido"}}),
        ("/score", {"pedido": {"DataRegistro": "15/09/2026"}}),
        ("/score", {"pedido": BASE | {"FoiReencaminhado": "Sim"}}),
        ("/score", {"pedido": BASE | {"surpresa": "texto"}}),
    ],
)
def test_real_bento_http_logs_once_with_actual_response_and_status(client, route, payload):
    http, recorder = client
    response = http.post(route, json=payload)
    events = [json.loads(line) for line in recorder.path.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    event = events[0]
    assert event["request_id"] == response.headers["x-request-id"]
    assert event["http_status"] == response.status_code
    assert event["model_version"] == "provided-2026-09-21"
    assert event["artifact_sha256"]
    assert event["latency_ms"] > 0
    if response.status_code == 200:
        assert event["response"] == response.json()
    else:
        assert event["outcome"] == "validation_error"
        assert event["error"]


def test_invalid_json_and_metrics_are_exposed_by_actual_bento(client):
    http, recorder = client
    response = http.post(
        "/score", content=b'{"pedido":', headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    event = json.loads(recorder.path.read_text(encoding="utf-8").splitlines()[0])
    assert event["error"]["kind"] == "invalid_json"
    http.post("/score", json={"pedido": BASE})
    metrics = http.get("/metrics")
    assert metrics.status_code == 200
    families = list(text_string_to_metric_families(metrics.text))
    assert {"lai_http_requests", "lai_score_failures", "lai_prediction_score"} <= {
        family.name for family in families
    }
    samples = [sample for family in families for sample in family.samples]
    assert any(
        sample.name == "lai_http_requests_total"
        and sample.labels["status_class"] == "4xx"
        and sample.value >= 1
        for sample in samples
    )
    assert len(recorder.path.read_text(encoding="utf-8").splitlines()) == 2


def test_timeout_and_unhandled_exception_use_the_emitted_status(tmp_path, monkeypatch):
    monkeypatch.setenv("MLOPS_TELEMETRY_PATH", str(tmp_path / "scoring.jsonl"))
    monkeypatch.setenv("MLOPS_REQUEST_TIMEOUT_SECONDS", "0.02")
    get_settings.cache_clear()

    async def app(scope, receive, send):
        if scope["path"] == "/score":
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("synthetic error")
        await JSONResponse({"ok": True})(scope, receive, send)

    middleware = TelemetryMiddleware(app)
    http = TestClient(middleware, raise_server_exceptions=False)
    try:
        assert http.post("/score").status_code == 504
        assert http.post("/score_baseline").status_code == 500
        events = [
            json.loads(line)
            for line in middleware.recorder.path.read_text(encoding="utf-8").splitlines()
        ]
        assert [e["http_status"] for e in events] == [504, 500]
        assert [e["error"]["kind"] for e in events] == ["timeout", "server_error"]
    finally:
        http.close()
        middleware.recorder.close()
        get_settings.cache_clear()
