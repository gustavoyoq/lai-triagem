"""Observabilidade: Prometheus e telemetria agregada, sem payloads ou identificadores."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from prometheus_client import Counter, Histogram

SCORES = Counter(
    "lai_score_requests_total", "Inferências concluídas", ["model_version", "known_organ"]
)
FAILURES = Counter("lai_score_failures_total", "Falhas por tipo", ["kind"])
LATENCY = Histogram("lai_score_latency_seconds", "Latência do endpoint de score")


class TelemetryRecorder:
    """Registra apenas bins e flags; desligado por padrão para proteção de dados."""

    def __init__(self, enabled: bool, path: Path) -> None:
        self.enabled = enabled
        self.path = path

    def record(
        self, *, score: float, model_version: str, organ_known: bool, history_supplied: bool
    ) -> None:
        SCORES.labels(model_version=model_version, known_organ=str(organ_known).lower()).inc()
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "model_version": model_version,
            "score_bin": min(int(score * 10), 9),
            "organ_known": organ_known,
            "history_supplied": history_supplied,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, separators=(",", ":")) + "\n")
