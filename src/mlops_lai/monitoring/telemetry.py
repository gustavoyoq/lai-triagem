"""Eventos JSONL append-only e métricas de inferência compatíveis com Prometheus."""

from __future__ import annotations

import json
import logging
import math
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from time import perf_counter_ns
from typing import Any
from uuid import uuid4

from prometheus_client import Counter, Histogram

from mlops_lai.core.settings import Settings, get_settings
from mlops_lai.features.preprocessor import LEAKAGE_FIELDS, REQUEST_FIELDS, Preprocessor

SCORES = Counter(
    "lai_score_requests_total", "Inferências /score concluídas", ["model_version", "known_organ"]
)
FAILURES = Counter("lai_score_failures_total", "Chamadas de inferência rejeitadas", ["kind"])
LATENCY = Histogram("lai_score_latency_seconds", "Duração de model.score em segundos")
HTTP_REQUESTS = Counter(
    "lai_http_requests_total", "Respostas HTTP concluídas", ["endpoint", "method", "status_class"]
)
HTTP_LATENCY = Histogram(
    "lai_http_request_duration_seconds",
    "Duração HTTP no servidor até o último envio de corpo",
    ["endpoint", "method"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20),
)
PREDICTIONS = Histogram(
    "lai_prediction_score",
    "Escores devolvidos por inferências concluídas",
    ["endpoint", "model_version"],
    buckets=tuple(i / 10 for i in range(11)),
)
RISKS = Counter(
    "lai_risk_predictions_total",
    "Classificação de risco devolvida",
    ["endpoint", "model_version", "risk"],
)
QUALITY = Counter("lai_input_quality_events_total", "Chamadas com indicador de qualidade", ["kind"])
WRITE_FAILURES = Counter("lai_telemetry_write_failures_total", "Falhas de gravação JSONL")

INFERENCE_ENDPOINTS = {"/score", "/score_baseline"}
CURRENT_EVENT: ContextVar[dict[str, Any] | None] = ContextVar("lai_event", default=None)


def timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def json_safe(value: Any) -> Any:
    """Converte NumPy e números não finitos para JSON estrito."""
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def selected_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Mantém somente campos analíticos e valores escalares do contrato."""
    return {
        key: value.strip() if isinstance(value, str) else value
        for key, value in payload.items()
        if key in REQUEST_FIELDS - {"DataNascimento"}
        and isinstance(value, (str, int, float, bool, type(None)))
    }


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(json_safe(record.msg), ensure_ascii=False, allow_nan=False)


class AppendHandler(logging.FileHandler):
    """FileHandler preservando sua sincronização e propagando falhas ao gravador."""

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802
        raise OSError("falha ao persistir evento de telemetria")


class TelemetryRecorder:
    def __init__(self, enabled: bool, path: Path) -> None:
        self.enabled = enabled
        self.instance_id = uuid4().hex
        self.path = path.with_name(f"{path.stem}.{self.instance_id}.{os.getpid()}.jsonl")
        self.preprocessor: Preprocessor | None = None
        self.model_version: str | None = None
        self.artifact_sha256: str | None = None
        self.logger = logging.getLogger(f"mlops_lai.events.{self.instance_id}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        if enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handler = AppendHandler(self.path, mode="a", encoding="utf-8")
            handler.setFormatter(JsonFormatter())
            self.logger.addHandler(handler)

    def new_event(self, endpoint: str, method: str | None = None) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "event_type": "inference_completed"
            if endpoint in INFERENCE_ENDPOINTS
            else "http_completed",
            "request_id": str(uuid4()),
            "instance_id": self.instance_id,
            "process_id": os.getpid(),
            "started_at_utc": timestamp(),
            "timestamp_utc": None,
            "endpoint": endpoint,
            "method": method,
            "transport": "http" if method else "python",
            "http_status": None,
            "outcome": "success",
            "latency_ms": None,
            "inference_ms": None,
            "model_version": self.model_version,
            "artifact_sha256": self.artifact_sha256,
            "categorical_features": self.preprocessor.categorical if self.preprocessor else [],
            "input": {},
            "features": None,
            "quality": {},
            "response": None,
            "prediction_raw": None,
            "error": None,
        }

    def capture_input(self, event: dict[str, Any], payload: dict[str, Any]) -> None:
        event["input"] = selected_input(payload)
        if self.preprocessor is not None:
            event["quality"] = self.preprocessor.monitoring_state(payload)

    def classify_error(self, event: dict[str, Any], kind: str) -> None:
        quality = event.get("quality") or {}
        event["error"] = {
            "kind": kind,
            "stage": event.get("stage", "http_validation"),
            "fields": quality.get("rejected_fields", []),
        }

    @contextmanager
    def operation(self, endpoint: str, payload: dict[str, Any]):
        """Reutiliza o contexto HTTP ou finaliza uma chamada direta de Python."""
        existing = CURRENT_EVENT.get()
        event = existing if existing is not None else self.new_event(endpoint)
        token = CURRENT_EVENT.set(event)
        start = perf_counter_ns()
        self.capture_input(event, payload)
        event["model_version"] = self.model_version
        event["artifact_sha256"] = self.artifact_sha256
        try:
            yield event
        except Exception:
            if event["error"] is None:
                rejected = set(payload) - REQUEST_FIELDS
                kind = "leakage_fields" if set(payload) & LEAKAGE_FIELDS else "model_error"
                if rejected and kind != "leakage_fields":
                    kind = "unknown_fields"
                self.classify_error(event, kind)
            event["outcome"] = (
                "server_error" if event["error"]["kind"] == "model_error" else "validation_error"
            )
            raise
        finally:
            if existing is None:
                self.finish(event, start)
            CURRENT_EVENT.reset(token)

    def finish(self, event: dict[str, Any], started_ns: int) -> None:
        event["timestamp_utc"] = timestamp()
        event["latency_ms"] = (perf_counter_ns() - started_ns) / 1_000_000
        event.pop("stage", None)
        endpoint = event["endpoint"]
        if event["transport"] == "http":
            status = event["http_status"]
            status_class = f"{status // 100}xx" if status is not None else "cancelled"
            HTTP_REQUESTS.labels(endpoint, event["method"], status_class).inc()
            HTTP_LATENCY.labels(endpoint, event["method"]).observe(event["latency_ms"] / 1000)
        if endpoint in INFERENCE_ENDPOINTS:
            self._observe_inference(event)
        if self.enabled:
            try:
                self.logger.info(event)
            except (OSError, ValueError, TypeError):
                WRITE_FAILURES.inc()
                logging.getLogger(__name__).exception("Falha ao gravar telemetria")

    @staticmethod
    def _observe_inference(event: dict[str, Any]) -> None:
        response = event.get("response") or {}
        quality = event["quality"]
        for key in ("unknown_organ", "history_absent", "history_partial", "unknown_categories"):
            if quality.get(key):
                QUALITY.labels(key).inc()
        if event["outcome"] != "success":
            FAILURES.labels((event.get("error") or {}).get("kind", "http_error")).inc()
            return
        if "probabilidade_reencaminhamento" not in response:
            return
        score = response["probabilidade_reencaminhamento"]
        version = response["model_version"]
        PREDICTIONS.labels(event["endpoint"], version).observe(score)
        RISKS.labels(event["endpoint"], version, response["alerta"]).inc()
        if event["endpoint"] == "/score":
            SCORES.labels(version, str(response["orgao_conhecido"]).lower()).inc()
            event.update(
                score_bin=min(int(score * 10), 9),
                organ_known=response["orgao_conhecido"],
                history_supplied=response["historico_informado"],
            )

    def close(self) -> None:
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)


@lru_cache
def _recorder(enabled: bool, path: Path) -> TelemetryRecorder:
    return TelemetryRecorder(enabled, path)


def get_recorder(settings: Settings | None = None) -> TelemetryRecorder:
    settings = settings or get_settings()
    recorder = _recorder(settings.enable_telemetry, settings.resolved_telemetry_path())
    if settings.enable_telemetry and not recorder.logger.handlers:
        _recorder.cache_clear()
        recorder = _recorder(settings.enable_telemetry, settings.resolved_telemetry_path())
    return recorder
